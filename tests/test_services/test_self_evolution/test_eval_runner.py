"""Tests for the OfflineEvaluationRunner gate adapter (1.3.6f eval wiring).

Covers: task resolution by agent binding, the bind/unbind plumb through
``EvaluationEngine.run`` (candidate rendered via ``CandidateSkillBinding``),
summary pass_rate extraction, and the None-degradation paths (no bound
task, non-agent answer source, cost guardrail). The engine itself is
stubbed — its behavior is covered by the 7.2c suite.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.models.evaluation import EvaluationTaskModel
from hecate.models.session import SessionModel  # noqa: F401  (register table)
from hecate.studio.self_evolution.eval_runner import (
    CandidateSkillBinding,
    OfflineEvaluationRunner,
)

WS = uuid.uuid4()
AGENT = uuid.uuid4()
DATASET = uuid.uuid4()

_CANDIDATE = SimpleNamespace(
    id=uuid.uuid4(),
    workspace_id=WS,
    name="verify-tool-args",
    procedure="Read the schema. Confirm required fields.",
    guardrails="Never guess argument formats.",
)


def _task(**config_extra) -> EvaluationTaskModel:
    return EvaluationTaskModel(
        workspace_id=WS,
        name="agent-regression",
        task_type="offline",
        status="active",
        evaluator_configs=["exact_match"],
        config={
            "dataset_id": str(DATASET),
            "answer_source": "agent",
            "agent_id": str(AGENT),
            "max_total_executions": 100,
            **config_extra,
        },
    )


@pytest.fixture()
def stub_engine(monkeypatch):
    """Replace EvaluationEngine with a stub capturing run kwargs."""
    calls: list[dict] = []

    class _StubResult:
        run_id = uuid.uuid4()
        metric_averages: dict = {}

    class _StubEngine:
        def __init__(self, db) -> None:
            pass

        async def run(self, **kwargs):
            calls.append(kwargs)
            run = kwargs["run"]
            run.summary = {"pass_rate": 0.9}
            return _StubResult()

    monkeypatch.setattr("hecate.ops.evaluation.engine.EvaluationEngine", _StubEngine)
    return calls


@pytest.fixture()
def stub_runner_helpers(monkeypatch):
    """Stub dataset scanning + evaluator resolution on OfflineTaskRunner.

    The adapter delegates these to the runner; their internals (item rows,
    evaluator registry) are 7.2c-suite territory — here they just return
    benign values so the adapter's own logic is exercised.
    """

    async def _fake_count(self, dataset_id):
        return 5

    monkeypatch.setattr(
        "hecate.ops.evaluation.tasks.runner.OfflineTaskRunner._count_items",
        _fake_count,
    )
    monkeypatch.setattr(
        "hecate.ops.evaluation.tasks.runner.OfflineTaskRunner._resolve_evaluators",
        lambda self, task: [SimpleNamespace()],
    )


class TestOfflineEvaluationRunner:
    async def test_no_bound_task_returns_none(self, db_session: AsyncSession, stub_engine) -> None:
        runner = OfflineEvaluationRunner(db_session)
        assert await runner(_CANDIDATE, bind_skill=True, agent_id=AGENT) is None
        assert stub_engine == []

    async def test_bound_task_runs_leg_with_binding(self, db_session, stub_engine, stub_runner_helpers) -> None:
        db_session.add(_task())
        await db_session.flush()
        runner = OfflineEvaluationRunner(db_session)

        score = await runner(_CANDIDATE, bind_skill=True, agent_id=AGENT)

        assert score == 0.9
        assert len(stub_engine) == 1
        binding = stub_engine[0]["agent_definition"]
        assert isinstance(binding, CandidateSkillBinding)
        assert "Read the schema." in binding.extra_skill_instructions
        assert "Never guess argument formats." in binding.extra_skill_instructions
        assert stub_engine[0]["agent_id"] == AGENT

    async def test_unbind_leg_passes_no_definition(self, db_session, stub_engine, stub_runner_helpers) -> None:
        db_session.add(_task())
        await db_session.flush()
        runner = OfflineEvaluationRunner(db_session)

        score = await runner(_CANDIDATE, bind_skill=False, agent_id=AGENT)

        assert score == 0.9
        assert stub_engine[0]["agent_definition"] is None

    async def test_non_agent_answer_source_returns_none(self, db_session, stub_engine, stub_runner_helpers) -> None:
        db_session.add(_task(answer_source="manual"))
        await db_session.flush()
        runner = OfflineEvaluationRunner(db_session)

        assert await runner(_CANDIDATE, bind_skill=True, agent_id=AGENT) is None
        assert stub_engine == []

    async def test_cost_guardrail_returns_none(self, db_session, stub_engine, stub_runner_helpers) -> None:
        db_session.add(_task(max_total_executions=3))
        await db_session.flush()
        runner = OfflineEvaluationRunner(db_session)

        assert await runner(_CANDIDATE, bind_skill=True, agent_id=AGENT) is None
        assert stub_engine == []

    async def test_task_bound_to_other_agent_not_used(self, db_session, stub_engine, stub_runner_helpers) -> None:
        other = uuid.uuid4()
        db_session.add(_task(agent_id=str(other)))
        await db_session.flush()
        runner = OfflineEvaluationRunner(db_session)

        assert await runner(_CANDIDATE, bind_skill=True, agent_id=AGENT) is None
        assert stub_engine == []


class TestMetricDirectionSafety:
    """Non-normalized metrics must never become the gate score (B4 hotfix).

    The gate compares higher-is-better with a 0-1 threshold: a latency-only
    task returning 900 as its score would make a slower run count as an
    improvement. Only pass_rate may stand in when the summary lacks it.
    """

    async def test_latency_metric_never_becomes_score(self, db_session, stub_engine, stub_runner_helpers) -> None:
        db_session.add(_task())
        # Strip the threshold from the task config so no summary pass_rate exists.
        task = (await db_session.execute(select(EvaluationTaskModel))).scalars().one()
        task.config.pop("threshold", None)
        await db_session.flush()

        # Engine stub with no summary and a latency-only metric average.
        class _LatencyResult:
            run_id = uuid.uuid4()
            metric_averages = {"latency_ms": 900.0}

        orig_run = stub_engine  # captured calls list

        import hecate.studio.self_evolution.eval_runner as er

        class _EngineNoSummary:
            def __init__(self, db) -> None:
                pass

            async def run(self, **kwargs):
                orig_run.append(kwargs)
                return _LatencyResult()

        import hecate.ops.evaluation.engine as engine_mod

        real_engine = engine_mod.EvaluationEngine
        engine_mod.EvaluationEngine = _EngineNoSummary
        try:
            runner = er.OfflineEvaluationRunner(db_session)
            score = await runner(_CANDIDATE, bind_skill=True, agent_id=AGENT)
        finally:
            engine_mod.EvaluationEngine = real_engine

        assert score is None  # direction unknown → skipped, not 900

    def test_pass_rate_metric_still_used(self) -> None:
        from types import SimpleNamespace

        from hecate.studio.self_evolution.eval_runner import _primary_metric_average

        result = SimpleNamespace(metric_averages={"pass_rate": 0.8})
        assert _primary_metric_average(result) == 0.8

        result = SimpleNamespace(metric_averages={"latency_ms": 900.0})
        assert _primary_metric_average(result) is None

        result = SimpleNamespace(metric_averages={})
        assert _primary_metric_average(result) is None
