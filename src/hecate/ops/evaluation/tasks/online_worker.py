"""Always-on online evaluation worker (ingest-then-score).

Consumes the ``traces`` table — the runtime persists every span there via
the OTel span processor, so scoring is a pure read-side consumer and the
request path is untouched. Each cycle, per enabled online task:

1. discover completed root traces created after the task's watermark,
   filtered by the agent under test via ``sessions.agent_id``
   (``traces.agent_id`` has no writer today);
2. deterministically sample by ``sampling_rate`` — the same trace ID always
   yields the same decision, so rescans never flip;
3. project the trace's time window into an ``EvalInput`` from the session's
   event log (:mod:`trace_input`) and score it with the task's evaluators;
4. persist target-typed score rows (deduped against
   ``(task_id, target_type, target_id, metric_name)`` — the unique index is
   the DB backstop, this worker is the single writer via the startup
   advisory lock) and advance the watermark.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import uuid
from datetime import UTC, datetime

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.models.evaluation import (
    EvaluationTaskModel,
    EvaluationTaskScoreModel,
    TaskScoreStatus,
    TaskStatus,
    TaskType,
)
from hecate.models.session import SessionModel
from hecate.models.trace import TraceModel
from hecate.ops.evaluation.evaluator import Evaluator
from hecate.ops.evaluation.tasks.trace_input import build_eval_input
from hecate.ops.evaluation.types import EvalInput, Score
from hecate.runtime.eventstore import EventStore

logger = logging.getLogger(__name__)

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_LOCK_KEY = int(hashlib.sha256(b"hecate:online-evaluation-worker").hexdigest()[:15], 16)


class OnlineEvaluationWorker:
    """Single-consumer scoring loop over completed production traces.

    Args:
        poll_interval_seconds: Delay between scan cycles.
        event_store: Optional EventStore override (tests inject an
            in-memory store); defaults to the settings-configured store.
    """

    def __init__(
        self,
        poll_interval_seconds: float = 30.0,
        event_store: EventStore | None = None,
    ) -> None:
        self.poll_interval_seconds = poll_interval_seconds
        self._event_store = event_store
        self._loop_task: asyncio.Task | None = None
        self._stop_event = asyncio.Event()
        self._lock_session: AsyncSession | None = None

    # -- lifecycle ----------------------------------------------------------

    async def start(self) -> bool:
        """Start the loop; return ``False`` when another node holds the lock.

        The PG session-level advisory lock is held for the worker's lifetime
        on its dedicated session and released on :meth:`stop`. On
        non-PostgreSQL dialects (unit tests on SQLite) the lock cannot
        exist — those are single-process environments, so the worker
        proceeds without cross-process exclusion.
        """
        self._stop_event.clear()
        if not await self._acquire_startup_lock():
            logger.info("Online evaluation worker not started — another node holds the lock")
            return False
        self._loop_task = asyncio.create_task(self._run_loop(), name="online-evaluation-worker")
        logger.info("Online evaluation worker started (interval %ss)", self.poll_interval_seconds)
        return True

    async def stop(self) -> None:
        """Signal the loop to exit and release the advisory lock."""
        self._stop_event.set()
        if self._loop_task is not None:
            self._loop_task.cancel()
            try:
                await self._loop_task
            except asyncio.CancelledError:
                pass
            except Exception:
                logger.exception("Online evaluation worker failed during shutdown")
            self._loop_task = None
        if self._lock_session is not None:
            await self._lock_session.close()
            self._lock_session = None

    async def _acquire_startup_lock(self) -> bool:
        from hecate.core.database import async_session_factory

        if async_session_factory is None:
            return True
        try:
            session = async_session_factory()
            result = await session.execute(text("SELECT pg_try_advisory_lock(:lock_id)"), {"lock_id": _LOCK_KEY})
        except Exception:
            logger.debug("Advisory lock unavailable — assuming single-process deployment")
            return True
        self._lock_session = session
        return bool(result.scalar_one())

    async def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                await self._cycle()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Online evaluation cycle failed")
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=self.poll_interval_seconds)
            except TimeoutError:
                continue

    # -- scan cycle ---------------------------------------------------------

    async def _cycle(self) -> None:
        from hecate.core.database import async_session_factory

        if async_session_factory is None:
            return

        async with async_session_factory() as session:
            result = await session.execute(
                select(EvaluationTaskModel).where(
                    EvaluationTaskModel.task_type == TaskType.ONLINE.value,
                    EvaluationTaskModel.status == TaskStatus.ACTIVE.value,
                    ~EvaluationTaskModel.deleted,
                )
            )
            tasks = list(result.scalars().all())

        for task in tasks:
            try:
                await self._process_task(task)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Online task %s cycle failed", task.id)

    async def _process_task(self, task: EvaluationTaskModel) -> None:
        from hecate.core.database import async_session_factory

        config = task.config or {}
        agent_id = uuid.UUID(str(config["agent_id"]))
        sampling_rate = float(config.get("sampling_rate", 1.0))
        max_traces = int(config.get("max_traces_per_cycle", 50))

        async with async_session_factory() as session:
            refreshed = await session.get(EvaluationTaskModel, task.id)
            if refreshed is None or refreshed.status != TaskStatus.ACTIVE.value:
                return
            task = refreshed

            candidates = await self._candidate_traces(session, agent_id, task.last_scanned_at, max_traces)
            sampled = [c for c in candidates if self._is_sampled(c.trace, sampling_rate)]

            metrics = dict(task.metrics or {})
            metrics["scanned"] = int(metrics.get("scanned", 0)) + len(candidates)
            metrics["sampled"] = int(metrics.get("sampled", 0)) + len(sampled)

            scored = 0
            errors = 0
            if sampled:
                evaluators = self._resolve_evaluators(task)
                existing = await self._existing_score_keys(session, task.id, [c.trace.id for c in sampled])
                event_store = await self._get_event_store()

                for candidate in sampled:
                    eval_input = await build_eval_input(
                        candidate.trace.session_id,
                        candidate.trace.start_time,
                        candidate.trace.end_time,
                        event_store,
                    )
                    if eval_input is None:
                        metrics["skipped"] = int(metrics.get("skipped", 0)) + 1
                        continue
                    for evaluator in evaluators:
                        if (candidate.trace.id, evaluator.name) in existing:
                            continue
                        score_rows, error_count = await self._score_trace(task, candidate, evaluator, eval_input)
                        for row in score_rows:
                            session.add(row)
                            existing.add((candidate.trace.id, row.metric_name))
                        scored += len(score_rows)
                        errors += error_count

            if candidates:
                task.last_scanned_at = max(c.trace.created_at for c in candidates)
            metrics["scored"] = int(metrics.get("scored", 0)) + scored
            metrics["errors"] = int(metrics.get("errors", 0)) + errors
            task.metrics = metrics
            await session.commit()

    async def _candidate_traces(
        self,
        session: AsyncSession,
        agent_id: uuid.UUID,
        watermark: datetime | None,
        max_traces: int,
    ) -> list[TraceCandidate]:
        """Completed root traces for the agent, oldest-first, capped.

        Agent ownership comes from the ``sessions`` join — the
        ``traces.agent_id`` column currently has no writer.
        """
        stmt = (
            select(TraceModel, SessionModel.agent_id)
            .join(SessionModel, SessionModel.id == TraceModel.session_id)
            .where(
                TraceModel.type == "trace",
                TraceModel.status == "completed",
                TraceModel.created_at > (watermark or _EPOCH),
                SessionModel.agent_id == agent_id,
                ~TraceModel.deleted,
            )
            .order_by(TraceModel.created_at.asc())
            .limit(max_traces)
        )
        rows = (await session.execute(stmt)).all()
        return [TraceCandidate(trace=trace, agent_id=agent_id_) for trace, agent_id_ in rows]

    def _is_sampled(self, trace: TraceModel, sampling_rate: float) -> bool:
        """Deterministic hash sampling — the same trace always decides the same way."""
        digest = hashlib.sha256(str(trace.id).encode()).hexdigest()
        return int(digest[:15], 16) % 1_000_000 < sampling_rate * 1_000_000

    def _resolve_evaluators(self, task: EvaluationTaskModel) -> list[Evaluator]:
        """Instantiate the task's evaluators, skipping unresolvable names."""
        from hecate.ops.evaluation.engine import get_evaluator_class

        evaluators: list[Evaluator] = []
        for name in task.evaluator_configs or []:
            cls = get_evaluator_class(name)
            if cls is None:
                logger.warning("Online task %s: evaluator %r not registered — skipped", task.id, name)
                continue
            evaluators.append(cls())
        return evaluators

    async def _existing_score_keys(
        self,
        session: AsyncSession,
        task_id: uuid.UUID,
        target_ids: list[uuid.UUID],
    ) -> set[tuple[uuid.UUID, str]]:
        """Score keys this task already produced for the sampled targets."""
        if not target_ids:
            return set()
        stmt = select(EvaluationTaskScoreModel.target_id, EvaluationTaskScoreModel.metric_name).where(
            EvaluationTaskScoreModel.task_id == task_id,
            EvaluationTaskScoreModel.target_id.in_(target_ids),
            ~EvaluationTaskScoreModel.deleted,
        )
        return {(target_id, metric_name) for target_id, metric_name in (await session.execute(stmt)).all()}

    async def _score_trace(
        self,
        task: EvaluationTaskModel,
        candidate: TraceCandidate,
        evaluator: Evaluator,
        eval_input: EvalInput,
    ) -> tuple[list[EvaluationTaskScoreModel], int]:
        """Score one trace with one evaluator; failures become error scores."""
        trace = candidate.trace
        try:
            output = await evaluator.evaluate(eval_input)
        except Exception as exc:
            logger.warning("Evaluator %s failed on trace %s: %s", evaluator.name, trace.id, exc)
            row = self._score_row(
                task,
                candidate,
                metric_name=evaluator.name,
                value=-1.0,
                reasoning=f"Evaluator error: {exc}",
                status=TaskScoreStatus.ERROR.value,
                source="llm_judge",
            )
            return [row], 1

        rows: list[EvaluationTaskScoreModel] = []
        error_count = 0
        for score in output.scores:
            try:
                if not isinstance(score, Score):
                    score = Score(
                        metric_name=getattr(score, "metric_name", evaluator.name),
                        value=float(getattr(score, "value", -1.0)),
                        reasoning=getattr(score, "reasoning", None),
                        source=getattr(score, "source", "llm_judge"),
                    )
            except (TypeError, ValueError) as exc:
                logger.warning("Evaluator %s produced invalid score on trace %s: %s", evaluator.name, trace.id, exc)
                error_count += 1
                rows.append(
                    self._score_row(
                        task,
                        candidate,
                        metric_name=getattr(score, "metric_name", evaluator.name),
                        value=-1.0,
                        reasoning=f"Invalid score: {exc}",
                        status=TaskScoreStatus.ERROR.value,
                        source="llm_judge",
                    )
                )
                continue
            rows.append(
                self._score_row(
                    task,
                    candidate,
                    metric_name=score.metric_name,
                    value=score.value,
                    reasoning=score.reasoning,
                    status=TaskScoreStatus.COMPLETED.value,
                    source=score.source,
                )
            )
        return rows, error_count

    def _score_row(
        self,
        task: EvaluationTaskModel,
        candidate: TraceCandidate,
        *,
        metric_name: str,
        value: float,
        reasoning: str | None,
        status: str,
        source: str,
    ) -> EvaluationTaskScoreModel:
        return EvaluationTaskScoreModel(
            task_id=task.id,
            target_type="trace",
            target_id=candidate.trace.id,
            session_id=candidate.trace.session_id,
            agent_id=candidate.agent_id,
            metric_name=metric_name,
            value=value,
            reasoning=reasoning,
            source=source,
            status=status,
            workspace_id=task.workspace_id,
        )

    async def _get_event_store(self) -> EventStore:
        if self._event_store is None:
            from hecate.core.config import settings
            from hecate.studio.event_state import create_event_store

            self._event_store = create_event_store(settings)
        return self._event_store


class TraceCandidate:
    """A sampled trace plus its owning agent (denormalized onto score rows)."""

    __slots__ = ("trace", "agent_id")

    def __init__(self, trace: TraceModel, agent_id: uuid.UUID) -> None:
        self.trace = trace
        self.agent_id = agent_id
