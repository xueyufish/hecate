"""Workspace-scoped business logic for managing scheduled tasks.

CRUD and lifecycle operations for scheduled tasks and their executions,
including state transitions and catch-up logic. REST callers always pass
their server-resolved ``workspace_id`` and re-resolve live rows under that
scope. Omitting the workspace preserves the existing privileged internal
service calls; REST must never use that compatibility path. The background
scheduler loop keeps its own raw queries and global duty.

Historical organization-level rows (``workspace_id IS NULL``) are
preserved untouched but are not visible through this workspace-scoped
management surface.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.models.agent import AgentModel
from hecate.models.scheduled_task import (
    ExecutionStatus,
    ScheduledTaskExecutionModel,
    ScheduledTaskModel,
    ScheduleState,
    TriggerType,
)
from hecate.models.workflow import WorkflowModel
from hecate.models.workspace import WorkspaceModel
from hecate.ops.scheduling.manager import ScheduleManager

logger = logging.getLogger(__name__)

# Row-level attribution keys. The executors treat ``agent_id`` /
# ``workflow_id`` in execution_config as target-selection fallbacks, so
# caller-supplied JSON must never carry them — the schedule row's own
# columns are the only target identity for REST-managed schedules.
RESERVED_EXECUTION_CONFIG_KEYS = frozenset({"org_id", "workspace_id", "agent_id", "workflow_id"})

TARGET_UNAVAILABLE_MESSAGE = "target binding is not available in this workspace"


class ScheduleTargetError(Exception):
    """A schedule's explicit agent/workflow binding is not live in its workspace.

    Uniform for missing, soft-deleted and cross-workspace targets so the
    caller cannot distinguish which one it hit.
    """

    def __init__(self, message: str = TARGET_UNAVAILABLE_MESSAGE) -> None:
        super().__init__(message)
        self.message = message


class ExecutionConfigError(Exception):
    """execution_config carries keys reserved for row-level attribution."""

    def __init__(self, keys: list[str]) -> None:
        message = "execution_config must not carry reserved keys: " + ", ".join(sorted(keys))
        super().__init__(message)
        self.message = message


class ScheduledTaskService:
    """Manage schedules with explicit REST scope and trusted internal compatibility.

    Args:
        db: Async SQLAlchemy session (request-scoped for REST callers).
        scheduler: Optional ScheduleManager whose external registrations
            (add/update/remove) run after scoped persistence succeeds.
    """

    def __init__(self, db: AsyncSession, scheduler: ScheduleManager | None = None) -> None:
        self._db = db
        self._scheduler = scheduler

    # ------------------------------------------------------------------
    # Scope resolution
    # ------------------------------------------------------------------

    async def is_task_visible(self, task_id: uuid.UUID, *, workspace_id: uuid.UUID) -> bool:
        """Minimal visibility probe: live row owned by this workspace."""
        stmt = select(ScheduledTaskModel.id).where(
            ScheduledTaskModel.id == task_id,
            ScheduledTaskModel.workspace_id == workspace_id,
            ScheduledTaskModel.deleted.is_(False),
        )
        return (await self._db.execute(stmt)).scalar_one_or_none() is not None

    async def _resolve_visible(
        self, task_id: uuid.UUID, *, workspace_id: uuid.UUID | None
    ) -> ScheduledTaskModel | None:
        stmt = select(ScheduledTaskModel).where(
            ScheduledTaskModel.id == task_id,
            ScheduledTaskModel.deleted.is_(False),
        )
        if workspace_id is not None:
            stmt = stmt.where(ScheduledTaskModel.workspace_id == workspace_id)
        # A query's predicate is fresh, but SQLAlchemy otherwise preserves
        # cached attributes, including a target binding that changed in storage.
        return (await self._db.execute(stmt.execution_options(populate_existing=True))).scalar_one_or_none()

    @staticmethod
    def _reject_reserved_config(execution_config: dict | None) -> None:
        if not execution_config:
            return
        reserved = RESERVED_EXECUTION_CONFIG_KEYS.intersection(execution_config)
        if reserved:
            raise ExecutionConfigError(sorted(reserved))

    async def _assert_binding_alive(
        self,
        *,
        workspace_id: uuid.UUID | None,
        agent_id: uuid.UUID | None = None,
        workflow_id: uuid.UUID | None = None,
    ) -> None:
        """Explicit bindings must be live rows owned by this workspace."""
        if workspace_id is None:
            return  # Historical organization-level internal schedules have no workspace binding.
        for model, target_id in ((AgentModel, agent_id), (WorkflowModel, workflow_id)):
            if target_id is None:
                continue
            found = await self._db.scalar(
                select(model.id).where(
                    model.id == target_id, model.workspace_id == workspace_id, model.deleted.is_(False)
                )
            )
            if found is None:
                raise ScheduleTargetError()

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    async def create_task(
        self,
        *,
        workspace_id: uuid.UUID | None = None,
        org_id: uuid.UUID | None = None,
        name: str,
        cron_expression: str,
        agent_id: uuid.UUID | None = None,
        workflow_id: uuid.UUID | None = None,
        execution_config: dict | None = None,
        max_concurrent_runs: int = 1,
        catch_up: bool = False,
        timezone: str = "UTC",
        description: str | None = None,
    ) -> ScheduledTaskModel:
        """Create a schedule under an explicit workspace or trusted organization scope.

        REST attribution comes from the live workspace row. Binding liveness
        and execution_config are checked before persistence or registration.
        Internal organization-level callers retain their explicit org_id.
        """
        if workspace_id is not None:
            org_id = await self._db.scalar(
                select(WorkspaceModel.org_id).where(
                    WorkspaceModel.id == workspace_id, WorkspaceModel.deleted.is_(False)
                )
            )
            if org_id is None:
                raise LookupError("workspace not found")
            self._reject_reserved_config(execution_config)
        elif org_id is None:
            raise ValueError("org_id is required for an internal organization-level schedule")
        await self._assert_binding_alive(workspace_id=workspace_id, agent_id=agent_id, workflow_id=workflow_id)

        next_run: datetime | None = None
        if self._scheduler:
            try:
                next_run = self._scheduler.calculate_next_run(cron_expression, timezone)
            except Exception:
                logger.warning("Failed to calculate next run for cron: %s", cron_expression)

        task = ScheduledTaskModel(
            org_id=org_id,
            workspace_id=workspace_id,
            name=name,
            description=description,
            cron_expression=cron_expression,
            agent_id=agent_id,
            workflow_id=workflow_id,
            execution_config=execution_config or {},
            max_concurrent_runs=max_concurrent_runs,
            catch_up=catch_up,
            timezone=timezone,
            next_run_at=next_run,
        )
        self._db.add(task)
        await self._db.flush()

        if self._scheduler:
            try:
                await self._scheduler.add_schedule(task)
            except Exception:
                logger.warning("Failed to register schedule for task %s", task.id)

        return task

    async def get_task(self, task_id: uuid.UUID, *, workspace_id: uuid.UUID | None = None) -> ScheduledTaskModel | None:
        """Get a live task; omitted workspace is reserved for trusted callers."""
        return await self._resolve_visible(task_id, workspace_id=workspace_id)

    async def list_tasks(
        self,
        org_id: uuid.UUID | None = None,
        *,
        workspace_id: uuid.UUID | None = None,
        state: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[ScheduledTaskModel], int]:
        """List live tasks by REST workspace or trusted internal organization."""
        if workspace_id is None and org_id is None:
            raise ValueError("workspace_id or org_id is required")
        stmt = select(ScheduledTaskModel).where(ScheduledTaskModel.deleted.is_(False))
        if workspace_id is not None:
            stmt = stmt.where(ScheduledTaskModel.workspace_id == workspace_id)
        if org_id is not None:
            stmt = stmt.where(ScheduledTaskModel.org_id == org_id)
        if state:
            stmt = stmt.where(ScheduledTaskModel.state == state)

        count_stmt = select(func.count()).select_from(stmt.subquery())
        total = (await self._db.execute(count_stmt)).scalar_one()

        offset = (page - 1) * page_size
        stmt = stmt.order_by(ScheduledTaskModel.created_at.desc()).offset(offset).limit(page_size)
        items = (await self._db.execute(stmt)).scalars().all()

        return list(items), total

    async def update_task(
        self,
        task_id: uuid.UUID,
        *,
        workspace_id: uuid.UUID | None = None,
        **kwargs: object,
    ) -> ScheduledTaskModel | None:
        """Update a live task scoped to the workspace.

        The row is re-resolved under the live scope (an API precheck
        object is never trusted), the target binding is re-verified
        before the update re-registers scheduler work, and
        execution_config must stay free of reserved keys.
        """
        task = await self._resolve_visible(task_id, workspace_id=workspace_id)
        if task is None:
            return None
        if workspace_id is not None:
            self._reject_reserved_config(kwargs.get("execution_config"))
        await self._assert_binding_alive(
            workspace_id=task.workspace_id, agent_id=task.agent_id, workflow_id=task.workflow_id
        )

        for key, value in kwargs.items():
            if value is not None and hasattr(task, key):
                setattr(task, key, value)

        # Recalculate next run if cron changed
        if "cron_expression" in kwargs and self._scheduler:
            try:
                task.next_run_at = self._scheduler.calculate_next_run(task.cron_expression, task.timezone)
            except Exception:
                logger.warning("Failed to recalculate next run for task %s", task_id)

        await self._db.flush()

        # Re-register with scheduler
        if self._scheduler:
            try:
                await self._scheduler.update_schedule(task)
            except Exception:
                logger.warning("Failed to update schedule for task %s", task_id)

        # Refresh the server-onupdate timestamp now: after commit it can
        # only be loaded via a sync lazy refresh, which explodes with
        # MissingGreenlet under asyncio (pydantic model_validate hits it).
        await self._db.refresh(task)
        return task

    async def delete_task(self, task_id: uuid.UUID, *, workspace_id: uuid.UUID | None = None) -> bool:
        """Soft-delete a live task scoped to the workspace.

        Deliberately no binding check: deleting (or pausing) a schedule
        whose target is gone must stay available for cleanup.
        """
        task = await self._resolve_visible(task_id, workspace_id=workspace_id)
        if task is None:
            return False

        task.deleted = True
        task.deleted_at = datetime.now(UTC)
        await self._db.flush()

        if self._scheduler:
            try:
                await self._scheduler.remove_schedule(task_id)
            except Exception:
                logger.warning("Failed to remove schedule for task %s", task_id)

        return True

    async def pause_task(
        self, task_id: uuid.UUID, *, workspace_id: uuid.UUID | None = None
    ) -> ScheduledTaskModel | None:
        """Pause an active task (no binding check — cleanup path)."""
        task = await self._resolve_visible(task_id, workspace_id=workspace_id)
        if task is None:
            return None
        return await self._apply_state(task, ScheduleState.PAUSED)

    async def resume_task(
        self, task_id: uuid.UUID, *, workspace_id: uuid.UUID | None = None
    ) -> ScheduledTaskModel | None:
        """Resume a paused task with optional catch-up.

        Re-enabling scheduled work re-verifies the target binding first:
        a schedule whose agent/workflow left the workspace resumes
        nothing and stays paused.
        """
        task = await self._resolve_visible(task_id, workspace_id=workspace_id)
        if task is None:
            return None
        await self._assert_binding_alive(
            workspace_id=task.workspace_id, agent_id=task.agent_id, workflow_id=task.workflow_id
        )
        task = await self._apply_state(task, ScheduleState.ACTIVE)

        # Re-register with scheduler
        if self._scheduler:
            try:
                await self._scheduler.add_schedule(task)
            except Exception:
                logger.warning("Failed to re-register schedule for task %s", task.id)

        # Catch-up: run missed executions if configured
        if task.catch_up and task.last_run_at:
            await self._catch_up_missed_runs(task)

        return task

    async def list_executions(
        self,
        task_id: uuid.UUID,
        *,
        workspace_id: uuid.UUID | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[ScheduledTaskExecutionModel], int] | None:
        """List executions of a live parent task scoped to the workspace.

        Returns None when the parent schedule is not visible under the
        scope (missing, soft-deleted, foreign) so the caller can surface
        one uniform 404 without querying history.
        """
        if await self._resolve_visible(task_id, workspace_id=workspace_id) is None:
            return None

        stmt = select(ScheduledTaskExecutionModel).where(ScheduledTaskExecutionModel.task_id == task_id)

        count_stmt = select(func.count()).select_from(stmt.subquery())
        total = (await self._db.execute(count_stmt)).scalar_one()

        offset = (page - 1) * page_size
        stmt = stmt.order_by(ScheduledTaskExecutionModel.created_at.desc()).offset(offset).limit(page_size)
        items = (await self._db.execute(stmt)).scalars().all()

        return list(items), total

    async def trigger_manual_run(
        self, task_id: uuid.UUID, *, workspace_id: uuid.UUID | None = None
    ) -> ScheduledTaskExecutionModel | None:
        """Record a manual trigger for a live task scoped to the workspace.

        Known limitation (deliberately out of scope here): this writes a
        record and never certifies that the agent/workflow execution ran
        — ``result_summary={"manual_trigger": True}`` marks a
        record-keeping entry, not an execution receipt. The binding is
        re-verified so a dead or foreign target cannot be triggered.
        """
        task = await self._resolve_visible(task_id, workspace_id=workspace_id)
        if task is None:
            return None
        await self._assert_binding_alive(
            workspace_id=task.workspace_id, agent_id=task.agent_id, workflow_id=task.workflow_id
        )

        execution = ScheduledTaskExecutionModel(
            task_id=task_id,
            started_at=datetime.now(UTC),
            status=ExecutionStatus.SUCCESS.value,
            triggered_by=TriggerType.MANUAL.value,
            result_summary={"manual_trigger": True},
            duration_ms=0,
        )
        self._db.add(execution)
        task.last_run_at = datetime.now(UTC)
        await self._db.flush()
        return execution

    async def _apply_state(self, task: ScheduledTaskModel, new_state: ScheduleState) -> ScheduledTaskModel:
        """Update task state."""
        task.state = new_state.value
        await self._db.flush()
        # Refresh the server-onupdate timestamp now: after commit it can
        # only be loaded via a sync lazy refresh, which explodes with
        # MissingGreenlet under asyncio (pydantic model_validate hits it).
        await self._db.refresh(task)
        return task

    async def _catch_up_missed_runs(self, task: ScheduledTaskModel) -> None:
        """Create pending executions for missed schedule times.

        Uses croniter to iterate from last_run_at to now.
        """
        try:
            from croniter import croniter

            if task.last_run_at is None:
                return

            now = datetime.now(UTC)
            cron = croniter(task.cron_expression, task.last_run_at)

            count = 0
            while count < 10:  # Limit catch-up to prevent runaway
                next_time = cron.get_next(datetime)
                if next_time > now:
                    break

                execution = ScheduledTaskExecutionModel(
                    task_id=task.id,
                    started_at=next_time,
                    completed_at=next_time,
                    status=ExecutionStatus.SKIPPED.value,
                    triggered_by=TriggerType.CRON.value,
                    result_summary={"catch_up": True},
                    duration_ms=0,
                )
                self._db.add(execution)
                count += 1

            if count:
                logger.info("Catch-up: created %d pending executions for task %s", count, task.id)
                await self._db.flush()

        except ImportError:
            logger.debug("croniter not available — skipping catch-up")
        except Exception as e:
            logger.error("Catch-up failed for task %s: %s", task.id, e)
