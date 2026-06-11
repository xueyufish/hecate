"""Business logic for managing scheduled tasks.

Provides CRUD operations for scheduled tasks and their executions,
including state transitions and catch-up logic.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.models.scheduled_task import (
    ExecutionStatus,
    ScheduledTaskExecutionModel,
    ScheduledTaskModel,
    ScheduleState,
    TriggerType,
)
from hecate.services.scheduling.manager import ScheduleManager

logger = logging.getLogger(__name__)


class ScheduledTaskService:
    """Service for managing scheduled task lifecycle.

    Args:
        db: Async SQLAlchemy session.
        scheduler: Optional ScheduleManager for APScheduler integration.
    """

    def __init__(self, db: AsyncSession, scheduler: ScheduleManager | None = None) -> None:
        self._db = db
        self._scheduler = scheduler

    async def create_task(
        self,
        *,
        name: str,
        cron_expression: str,
        org_id: uuid.UUID,
        workspace_id: uuid.UUID | None = None,
        agent_id: uuid.UUID | None = None,
        workflow_id: uuid.UUID | None = None,
        execution_config: dict | None = None,
        max_concurrent_runs: int = 1,
        catch_up: bool = False,
        timezone: str = "UTC",
        description: str | None = None,
    ) -> ScheduledTaskModel:
        """Create a new scheduled task and register it with the scheduler."""
        # Calculate next run time
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

        # Register with APScheduler
        if self._scheduler:
            try:
                await self._scheduler.add_schedule(task)
            except Exception:
                logger.warning("Failed to register schedule for task %s", task.id)

        return task

    async def get_task(self, task_id: uuid.UUID) -> ScheduledTaskModel | None:
        """Get a task by ID."""
        result = await self._db.execute(select(ScheduledTaskModel).where(ScheduledTaskModel.id == task_id))
        return result.scalar_one_or_none()

    async def list_tasks(
        self,
        org_id: uuid.UUID,
        *,
        workspace_id: uuid.UUID | None = None,
        state: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[ScheduledTaskModel], int]:
        """List tasks with optional filters and pagination."""
        stmt = select(ScheduledTaskModel).where(ScheduledTaskModel.org_id == org_id)
        if workspace_id:
            stmt = stmt.where(ScheduledTaskModel.workspace_id == workspace_id)
        if state:
            stmt = stmt.where(ScheduledTaskModel.state == state)

        count_stmt = select(func.count()).select_from(stmt.subquery())
        total = (await self._db.execute(count_stmt)).scalar_one()

        offset = (page - 1) * page_size
        stmt = stmt.order_by(ScheduledTaskModel.created_at.desc()).offset(offset).limit(page_size)
        result = await self._db.execute(stmt)
        items = result.scalars().all()

        return list(items), total

    async def update_task(
        self,
        task_id: uuid.UUID,
        **kwargs: object,
    ) -> ScheduledTaskModel | None:
        """Update a task and re-register with scheduler if cron changed."""
        task = await self.get_task(task_id)
        if task is None:
            return None

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

        return task

    async def delete_task(self, task_id: uuid.UUID) -> bool:
        """Soft-delete a task and remove its schedule."""
        task = await self.get_task(task_id)
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

    async def pause_task(self, task_id: uuid.UUID) -> ScheduledTaskModel | None:
        """Pause an active task."""
        return await self._update_state(task_id, ScheduleState.PAUSED)

    async def resume_task(self, task_id: uuid.UUID) -> ScheduledTaskModel | None:
        """Resume a paused task with optional catch-up."""
        task = await self._update_state(task_id, ScheduleState.ACTIVE)
        if task is None:
            return None

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
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[ScheduledTaskExecutionModel], int]:
        """List executions for a task with pagination."""
        stmt = select(ScheduledTaskExecutionModel).where(ScheduledTaskExecutionModel.task_id == task_id)

        count_stmt = select(func.count()).select_from(stmt.subquery())
        total = (await self._db.execute(count_stmt)).scalar_one()

        offset = (page - 1) * page_size
        stmt = stmt.order_by(ScheduledTaskExecutionModel.created_at.desc()).offset(offset).limit(page_size)
        result = await self._db.execute(stmt)
        items = result.scalars().all()

        return list(items), total

    async def trigger_manual_run(self, task_id: uuid.UUID) -> ScheduledTaskExecutionModel | None:
        """Manually trigger a task execution."""
        task = await self.get_task(task_id)
        if task is None:
            return None

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

    async def _update_state(self, task_id: uuid.UUID, new_state: ScheduleState) -> ScheduledTaskModel | None:
        """Update task state."""
        task = await self.get_task(task_id)
        if task is None:
            return None

        task.state = new_state.value
        await self._db.flush()
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
