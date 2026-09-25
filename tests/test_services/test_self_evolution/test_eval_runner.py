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
