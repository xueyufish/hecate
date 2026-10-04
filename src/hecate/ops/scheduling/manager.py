"""Scheduled task manager wrapping APScheduler with PostgreSQL job store.

Provides cron-based scheduling with:

- :class:`ScheduleManager` — manages APScheduler lifecycle and job CRUD
- Advisory lock support for multi-node execution
- Cron expression validation via croniter
- Trigger dispatch through the executor registry (step6 wiring): a cron
  fire resolves the scheduled task's target (agent/workflow) and runs it
  through the registered executor — the same entry-service path as every
  migrated entry chain. No execution happens without an executor result.
- Graceful degradation when apscheduler is not installed
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select, text

from hecate.core.database import async_session_factory
from hecate.models.scheduled_task import (
    ExecutionStatus,
    ScheduledTaskExecutionModel,
    ScheduledTaskModel,
    ScheduleState,
    TriggerType,
)

logger = logging.getLogger(__name__)

# Lazy import guard — apscheduler may not be installed
_apscheduler: object | None = None
_croniter: object | None = None

# Process-level executor registry: built once, reused by every cron fire.
_executor_registry: object | None = None


def _get_executor_registry() -> object:
    """Lazily create the default executor registry (agent + workflow)."""

    global _executor_registry  # noqa: PLW0603
    if _executor_registry is None:
        from hecate.ops.scheduling.executors import create_default_registry

        _executor_registry = create_default_registry()
    return _executor_registry


def set_executor_registry(registry: object) -> None:
    """Override the process registry (tests inject stub executors)."""

    global _executor_registry  # noqa: PLW0603
    _executor_registry = registry


def _get_croniter() -> object:
    """Lazy import of croniter."""
    global _croniter  # noqa: PLW0603
    if _croniter is None:
        try:
            import croniter as _ci

            _croniter = _ci
        except ImportError as err:
            raise ImportError(
                "croniter is required for scheduling. Install with: pip install hecate[scheduling]"
            ) from err
    return _croniter


class ScheduleManager:
    """Manage cron-based task scheduling with APScheduler.

    Wraps APScheduler with PostgreSQL job store for durable scheduling.
    Gracefully degrades when apscheduler is not installed.

    Args:
        database_url: SQLAlchemy connection string for the job store.
    """

    def __init__(self, database_url: str | None = None) -> None:
        self._database_url = database_url
        self._scheduler: object | None = None

    def _get_scheduler(self) -> object:
        """Lazily create and return the APScheduler instance."""
        if self._scheduler is not None:
            return self._scheduler

        try:
            from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
            from apscheduler.schedulers.asyncio import AsyncIOScheduler
        except ImportError as err:
            raise ImportError(
                "apscheduler is required for scheduling. Install with: pip install hecate[scheduling]"
            ) from err

        jobstores: dict[str, object] = {
            "default": SQLAlchemyJobStore(url=self._database_url or "sqlite:///jobs.sqlite")
        }
        self._scheduler = AsyncIOScheduler(jobstores=jobstores)
        return self._scheduler

    async def start(self) -> None:
        """Start the scheduler."""
        try:
            scheduler = self._get_scheduler()
            scheduler.start()
            logger.info("ScheduleManager started")
        except ImportError:
            logger.info("ScheduleManager disabled — apscheduler not installed")

    async def stop(self) -> None:
        """Shutdown the scheduler gracefully."""
        if self._scheduler is not None:
            self._scheduler.shutdown(wait=True)
            self._scheduler = None
            logger.info("ScheduleManager stopped")

    def validate_cron(self, expression: str) -> bool:
        """Validate a cron expression.

        Args:
            expression: 5-field cron expression (e.g. ``*/5 * * * *``).

        Returns:
            True if valid, False otherwise.
        """
        try:
            croniter_mod = _get_croniter()
            croniter_cls = croniter_mod.croniter
            croniter_cls(expression)
            return True
        except (ValueError, ImportError):
            return False

    def calculate_next_run(self, cron_expression: str, timezone: str = "UTC") -> datetime:
        """Calculate the next run time for a cron expression.

        Args:
            cron_expression: Valid 5-field cron expression.
            timezone: Timezone for calculation.

        Returns:
            The next scheduled datetime.
        """
        croniter_mod = _get_croniter()
        croniter_cls = croniter_mod.croniter
        now = datetime.now(UTC)
        cron = croniter_cls(cron_expression, now)
        return cron.get_next(datetime)

    async def add_schedule(self, task: ScheduledTaskModel) -> None:
        """Add a scheduled task to APScheduler.

        Args:
            task: The task model with cron expression and execution config.
        """
        try:
            from apscheduler.triggers.cron import CronTrigger

            scheduler = self._get_scheduler()

            parts = task.cron_expression.split()
            trigger = CronTrigger(
                minute=parts[0],
                hour=parts[1],
                day=parts[2],
                month=parts[3],
                day_of_week=parts[4],
                timezone=task.timezone or "UTC",
            )

            scheduler.add_job(
                self._execute_task,
                trigger=trigger,
                id=str(task.id),
                name=task.name,
                kwargs={"task_id": task.id},
                replace_existing=True,
            )
            logger.info("Added schedule for task %s (%s)", task.id, task.cron_expression)
        except ImportError:
            logger.debug("APScheduler not available — schedule not added")

    async def remove_schedule(self, task_id: uuid.UUID) -> None:
        """Remove a scheduled task from APScheduler.

        Args:
            task_id: UUID of the task to remove.
        """
        try:
            scheduler = self._get_scheduler()
            scheduler.remove_job(str(task_id))
            logger.info("Removed schedule for task %s", task_id)
        except Exception:
            logger.debug("Failed to remove schedule for task %s (may not exist)", task_id)

    async def update_schedule(self, task: ScheduledTaskModel) -> None:
        """Update a schedule by removing and re-adding it.

        Args:
            task: Updated task model.
        """
        await self.remove_schedule(task.id)
        if task.enabled and task.state == ScheduleState.ACTIVE.value:
            await self.add_schedule(task)

    async def _execute_task(self, task_id: uuid.UUID) -> None:
        """Execute a scheduled task (called by APScheduler).

        Includes advisory lock check for multi-node safety and
        max_concurrent_runs enforcement. The trigger dispatches through the
        executor registry (agent/workflow target resolution); the execution
        record reflects the executor's real result — there is no
        record-success-without-executing path.

        Args:
            task_id: UUID of the task to execute.
        """
        async with async_session_factory() as session:
            # Load task
            result = await session.execute(select(ScheduledTaskModel).where(ScheduledTaskModel.id == task_id))
            task = result.scalar_one_or_none()
            if task is None or not task.enabled:
                return

            # Check max_concurrent_runs
            active_count = await session.execute(
                select(func.count())
                .select_from(ScheduledTaskExecutionModel)
                .where(
                    ScheduledTaskExecutionModel.task_id == task_id,
                    ScheduledTaskExecutionModel.started_at.isnot(None),
                    ScheduledTaskExecutionModel.completed_at.is_(None),
                )
            )
            if (active_count.scalar_one() or 0) >= task.max_concurrent_runs:
                logger.debug("Skipping task %s — max concurrent runs reached", task_id)
                execution = ScheduledTaskExecutionModel(
                    task_id=task_id,
                    status=ExecutionStatus.SKIPPED.value,
                    triggered_by=TriggerType.CRON.value,
                )
                session.add(execution)
                await session.commit()
                return

            # Advisory lock for multi-node safety (PostgreSQL only; a
            # single-process sqlite profile has no competing node)
            acquired = True
            lock_id = 0
            if session.bind is not None and session.bind.dialect.name == "postgresql":
                lock_id = int(hashlib.sha256(f"{task_id}:{datetime.now(UTC).date()}".encode()).hexdigest()[:15], 16)
                lock_result = await session.execute(text("SELECT pg_try_advisory_lock(:lock_id)"), {"lock_id": lock_id})
                acquired = bool(lock_result.scalar_one())
            if not acquired:
                logger.debug("Skipping task %s — another node is executing", task_id)
                return

            try:
                started = datetime.now(UTC)
                execution = ScheduledTaskExecutionModel(
                    task_id=task_id,
                    started_at=started,
                    status=ExecutionStatus.SUCCESS.value,
                    triggered_by=TriggerType.CRON.value,
                )
                session.add(execution)

                # Dispatch through the executor registry: the scheduled row's
                # target binding decides which executor runs; the registry
                # runs it through the platform entry service (real
                # execution + Task/Run correlation), never a stub success.
                registry = _get_executor_registry()
                if task.agent_id is not None:
                    executor_type = "agent"
                elif task.workflow_id is not None:
                    executor_type = "workflow"
                else:
                    raise ValueError("scheduled task has neither agent_id nor workflow_id; cannot resolve executor")
                executor = registry.get(executor_type)
                if executor is None:
                    raise ValueError(f"no executor registered for task type {executor_type!r}")
                executor_result = await executor.execute(
                    task_id,
                    dict(task.execution_config or {}),
                    workspace_id=task.workspace_id,
                    agent_id=task.agent_id,
                    workflow_id=task.workflow_id,
                )
                success = isinstance(executor_result, dict) and executor_result.get("status") == "success"
                execution.status = ExecutionStatus.SUCCESS.value if success else ExecutionStatus.FAILED.value
                execution.result_summary = executor_result if isinstance(executor_result, dict) else None
                if not success:
                    error = executor_result.get("error") if isinstance(executor_result, dict) else None
                    execution.error_message = str(error or "executor reported a non-success status")

                # Update task last_run_at and next_run_at. The next-run
                # projection needs croniter (optional extra); without it the
                # run keeps its previous projection — the executed result
                # must not be marked failed over a missing calendar helper.
                task.last_run_at = started
                try:
                    task.next_run_at = self.calculate_next_run(task.cron_expression, task.timezone)
                except ImportError:
                    logger.debug("croniter not installed; next_run_at left unchanged for task %s", task_id)

                await session.commit()
                logger.info(
                    "Executed scheduled task %s via %s executor: %s",
                    task_id,
                    executor_type,
                    execution.status,
                )
            except Exception as e:
                logger.error("Scheduled task %s execution failed: %s", task_id, e)
                execution.status = ExecutionStatus.FAILED.value
                execution.error_message = str(e)
                await session.commit()
            finally:
                if lock_id:
                    await session.execute(text("SELECT pg_advisory_unlock(:lock_id)"), {"lock_id": lock_id})
                await session.commit()
