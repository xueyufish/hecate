"""Tests for the online evaluation worker (sampling, watermark, dedupe, isolation).

The worker reads its sessions from ``hecate.core.database.async_session_factory``;
tests patch that module attribute with a factory bound to the test engine (the
same in-memory SQLite the ``db_session`` fixture uses), and inject an
``InMemoryEventStore`` so no PostgreSQL is involved.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from hecate.models.evaluation import (
    EvaluationTaskModel,
    EvaluationTaskScoreModel,
    TaskScoreStatus,
    TaskType,
)
from hecate.models.session import SessionModel
from hecate.models.trace import TraceModel
from hecate.ops.evaluation.evaluator import Evaluator
from hecate.ops.evaluation.tasks.online_worker import OnlineEvaluationWorker
from hecate.ops.evaluation.types import EvalInput, EvalOutput, Score
from hecate.runtime.eventstore import Event, EventType, InMemoryEventStore

_AGENT = uuid.UUID("00000000-0000-0000-0000-0000000000aa")
_OTHER_AGENT = uuid.UUID("00000000-0000-0000-0000-0000000000bb")
_NOW = datetime(2026, 9, 10, 12, 0, 0, tzinfo=UTC)


class _StubEvaluator(Evaluator):
    @property
    def name(self) -> str:
        return "stub"

    @property
    def description(self) -> str:
        return "stub"

    async def evaluate(self, input: EvalInput) -> EvalOutput:  # noqa: A002 — ABC signature
        return EvalOutput(scores=[Score(metric_name="stub", value=0.8, source="deterministic")])


class _ExplodingEvaluator(Evaluator):
    @property
    def name(self) -> str:
        return "exploding"

    @property
    def description(self) -> str:
        return "always raises"

    async def evaluate(self, input: EvalInput) -> EvalOutput:  # noqa: A002 — ABC signature
        raise RuntimeError("judge unavailable")


@pytest.fixture
def patch_session_factory(monkeypatch: pytest.MonkeyPatch, db_session: AsyncSession):
    """Point the worker at the test engine."""
    factory = async_sessionmaker(db_session.bind, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr("hecate.core.database.async_session_factory", factory)
    return factory


@pytest.fixture(autouse=True)
def seeded_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    """Seed the engine's evaluator class index with the test doubles."""
    import hecate.ops.evaluation.engine as engine_mod

    monkeypatch.setattr(
        engine_mod,
        "_EVALUATOR_CLASS_REGISTRY",
        {"stub": _StubEvaluator, "exploding": _ExplodingEvaluator},
    )


async def _seed_session_and_trace(
    db_session: AsyncSession,
    *,
    agent_id: uuid.UUID = _AGENT,
    created_at: datetime = _NOW,
    status: str = "completed",
    trace_type: str = "trace",
) -> TraceModel:
    session_row = SessionModel(conversation_id=uuid.uuid4(), agent_id=agent_id)
    db_session.add(session_row)
    await db_session.flush()

    trace = TraceModel(
        trace_id=uuid.uuid4(),
        type=trace_type,
        name=f"session:{session_row.id}",
        session_id=session_row.id,
        status=status,
        start_time=created_at,
        end_time=created_at + timedelta(seconds=5),
        created_at=created_at,
    )
    db_session.add(trace)
    await db_session.flush()
    return trace


async def _seed_task(db_session: AsyncSession, **config_overrides) -> EvaluationTaskModel:
    config = {
        "agent_id": str(_AGENT),
        "sampling_rate": 1.0,
        "max_traces_per_cycle": 50,
    }
    config.update(config_overrides)
    task = EvaluationTaskModel(
        task_type=TaskType.ONLINE.value,
        name="prod-scoring",
        evaluator_configs=["stub"],
        config=config,
    )
    db_session.add(task)
    await db_session.flush()
    return task


async def _seed_conversation_events(session_id: uuid.UUID, base: datetime) -> InMemoryEventStore:
    store = InMemoryEventStore()
    events = [
        Event(
            session_id=session_id,
            superstep=1,
            event_type=EventType.LLM_REQUEST,
            timestamp=base + timedelta(seconds=1),
            payload={"messages": [{"role": "user", "content": "hello"}]},
        ),
        Event(
            session_id=session_id,
            superstep=1,
            event_type=EventType.LLM_REQUEST,
            timestamp=base + timedelta(seconds=2),
            payload={"messages": [{"role": "assistant", "content": "hi there"}]},
        ),
    ]
    for event in events:
        await store.append(event)
    return store


async def _scores(db_session: AsyncSession) -> list[EvaluationTaskScoreModel]:
    result = await db_session.execute(select(EvaluationTaskScoreModel))
    return list(result.scalars().all())


class TestSampling:
    async def test_rate_one_scores_all(self, db_session, patch_session_factory) -> None:
        trace = await _seed_session_and_trace(db_session)
        task = await _seed_task(db_session)
        store = await _seed_conversation_events(trace.session_id, trace.start_time)

        worker = OnlineEvaluationWorker(event_store=store)
        await worker._process_task(task)

        scores = await _scores(db_session)
        assert len(scores) == 1
        assert scores[0].target_id == trace.id
        assert scores[0].session_id == trace.session_id
        assert scores[0].agent_id == _AGENT
        assert scores[0].value == pytest.approx(0.8)
        assert scores[0].status == TaskScoreStatus.COMPLETED.value
        assert scores[0].metric_name == "stub"

    async def test_deterministic_and_bounded_by_rate(self, db_session) -> None:
        worker = OnlineEvaluationWorker()
        trace = TraceModel(id=uuid.UUID("00000000-0000-0000-0000-000000000042"))

        assert worker._is_sampled(trace, 1.0) is True
        assert worker._is_sampled(trace, 0.0) is False
        assert worker._is_sampled(trace, 0.5) == worker._is_sampled(trace, 0.5)

    async def test_volume_cap_limits_candidates(self, db_session, patch_session_factory) -> None:
        for i in range(3):
            await _seed_session_and_trace(db_session, created_at=_NOW + timedelta(minutes=i))
        await _seed_task(db_session)

        worker = OnlineEvaluationWorker()
        async with patch_session_factory() as session:
            candidates = await worker._candidate_traces(session, _AGENT, None, max_traces=2)

        assert len(candidates) == 2

    async def test_agent_filter_excludes_other_agents(self, db_session, patch_session_factory) -> None:
        await _seed_session_and_trace(db_session, agent_id=_OTHER_AGENT)

        worker = OnlineEvaluationWorker()
        async with patch_session_factory() as session:
            candidates = await worker._candidate_traces(session, _AGENT, None, max_traces=50)

        assert candidates == []


class TestWatermarkAndDedupe:
    async def test_watermark_advances_and_rescan_skips(self, db_session, patch_session_factory) -> None:
        trace = await _seed_session_and_trace(db_session)
        task = await _seed_task(db_session)
        store = await _seed_conversation_events(trace.session_id, trace.start_time)

        worker = OnlineEvaluationWorker(event_store=store)
        await worker._process_task(task)

        async with patch_session_factory() as session:
            refreshed = await session.get(EvaluationTaskModel, task.id)
            assert refreshed.last_scanned_at is not None
            assert refreshed.metrics["scored"] == 1
            assert refreshed.metrics["scanned"] == 1

        # Rewind the watermark to force a rescan: dedupe must prevent new rows.
        async with patch_session_factory() as session:
            refreshed = await session.get(EvaluationTaskModel, task.id)
            refreshed.last_scanned_at = None
            await session.commit()

        await worker._process_task(task)
        assert len(await _scores(db_session)) == 1

    async def test_disabled_task_not_processed(self, db_session, patch_session_factory) -> None:
        trace = await _seed_session_and_trace(db_session)
        task = await _seed_task(db_session)
        store = await _seed_conversation_events(trace.session_id, trace.start_time)

        async with patch_session_factory() as session:
            refreshed = await session.get(EvaluationTaskModel, task.id)
            refreshed.status = "disabled"
            await session.commit()

        worker = OnlineEvaluationWorker(event_store=store)
        await worker._process_task(task)
        assert await _scores(db_session) == []


class TestFailureIsolation:
    async def test_evaluator_error_becomes_error_score(self, db_session, patch_session_factory) -> None:
        trace = await _seed_session_and_trace(db_session)
        config = {"agent_id": str(_AGENT), "sampling_rate": 1.0, "max_traces_per_cycle": 50}
        task = EvaluationTaskModel(
            task_type=TaskType.ONLINE.value,
            name="with-failing",
            evaluator_configs=["stub", "exploding"],
            config=config,
        )
        db_session.add(task)
        await db_session.flush()
        store = await _seed_conversation_events(trace.session_id, trace.start_time)

        worker = OnlineEvaluationWorker(event_store=store)
        await worker._process_task(task)

        scores = await _scores(db_session)
        by_metric = {s.metric_name: s for s in scores}
        assert set(by_metric) == {"stub", "exploding"}
        assert by_metric["stub"].value == pytest.approx(0.8)
        assert by_metric["exploding"].status == TaskScoreStatus.ERROR.value
        assert by_metric["exploding"].value == -1.0
        assert "judge unavailable" in (by_metric["exploding"].reasoning or "")

        async with patch_session_factory() as session:
            refreshed = await session.get(EvaluationTaskModel, task.id)
            assert refreshed.metrics["errors"] == 1


class TestLifecycle:
    async def test_start_proceeds_without_pg_and_stop_cleans_up(self, db_session, patch_session_factory) -> None:
        worker = OnlineEvaluationWorker(poll_interval_seconds=0.01)
        assert await worker.start() is True
        assert worker._loop_task is not None
        await worker.stop()
        assert worker._loop_task is None
        assert worker._lock_session is None
