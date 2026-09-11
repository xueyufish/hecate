"""Async execution for offline task runs.

Mirrors the synthesis-job lifecycle (``queued → running → completed/failed``,
:func:`asyncio.create_task` + a fresh session per execution): the API request
persists a ``queued`` run row and commits, then hands off here; the spawned
task reopens the run, executes the task's evaluators against its dataset via
:class:`EvaluationEngine`, and commits the terminal state.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from hecate.models.evaluation import (
    EvaluationRunModel,
    EvaluationTaskModel,
    RunStatus,
)
from hecate.ops.evaluation.engine import EvaluationEngine, get_evaluator_class
from hecate.ops.evaluation.evaluator import Evaluator
from hecate.ops.evaluation.types import AnswerSource

logger = logging.getLogger(__name__)


class OfflineTaskRunner:
    """Execute offline task runs in the background.

    Args:
        db: Async SQLAlchemy session. As with the synthesis job service,
            the background task opens its own session via
            ``async_session_factory`` so the request's session can close
            cleanly while the run executes.
    """

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def run_in_background(self, run_id: uuid.UUID, task_id: uuid.UUID) -> None:
        """Spawn an asyncio task executing the run; returns immediately."""
        asyncio.create_task(self._execute(run_id, task_id))

    async def _execute(self, run_id: uuid.UUID, task_id: uuid.UUID) -> None:
        from hecate.core.database import async_session_factory  # local import — avoid circular

        if async_session_factory is None:
            logger.error("async_session_factory unavailable — cannot run task run %s", run_id)
            return

        async with async_session_factory() as session:
            run = await session.get(EvaluationRunModel, run_id)
            task = await session.get(EvaluationTaskModel, task_id)
            if run is None or task is None:
                logger.error("Task run %s (task %s) not found", run_id, task_id)
                return

            run.status = RunStatus.RUNNING.value
            run.started_at = datetime.now(UTC)
            await session.flush()

            try:
                evaluators = self._resolve_evaluators(task)
                if not evaluators:
                    raise RuntimeError("no evaluators could be resolved: " + ", ".join(task.evaluator_configs or []))

                config = task.config or {}
                engine = EvaluationEngine(session)
                await engine.run(
                    evaluators,
                    dataset_id=run.dataset_id,
                    answer_source=AnswerSource(config.get("answer_source") or "manual"),
                    tags=config.get("tags"),
                    agent_id=uuid.UUID(str(config["agent_id"])) if config.get("agent_id") else None,
                    run=run,
                    summary_config=self._summary_config(task),
                )
                # engine.run marked the run completed; persist its scores + summary
                await session.commit()
            except Exception:  # noqa: BLE001 — surface error state on the run row
                logger.exception("Task run %s failed", run_id)
                run.status = RunStatus.FAILED.value
                run.completed_at = datetime.now(UTC)
                await session.commit()

    def _resolve_evaluators(self, task: EvaluationTaskModel) -> list[Evaluator]:
        """Instantiate the task's evaluators, skipping unresolvable names.

        Trigger-time validation normally guarantees resolvability; between
        trigger and execution the registry can change (e.g. an optional
        dependency went missing at restart), so unresolvable names are
        skipped and only fail the run when nothing resolves.
        """
        evaluators: list[Evaluator] = []
        for name in task.evaluator_configs or []:
            cls = get_evaluator_class(name)
            if cls is None:
                logger.warning("Task %s references unregistered evaluator %r — skipped", task.id, name)
                continue
            evaluators.append(cls())
        return evaluators

    def _summary_config(self, task: EvaluationTaskModel) -> dict | None:
        """Build the engine ``summary_config`` from the task config.

        Pass/fail needs ``threshold``; regression flags need
        ``baseline_run_id``. Without either, no summary is produced.
        """
        config = task.config or {}
        summary_config: dict = {}
        if config.get("threshold") is not None:
            summary_config["threshold"] = float(config["threshold"])
        if config.get("baseline_run_id"):
            summary_config["baseline_run_id"] = str(config["baseline_run_id"])
        if not summary_config:
            return None
        summary_config["regression_threshold"] = float(config.get("regression_threshold") or 0.05)
        return summary_config
