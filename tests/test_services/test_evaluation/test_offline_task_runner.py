"""Tests for the offline task runner and the engine's task-run extensions.

Covers the ``answer_source="agent"`` path (with a stubbed agent invocation),
agent-failure isolation, threshold/baseline summaries, and the reuse of a
pre-created run row.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from hecate.models.evaluation import (
    EvaluationItemModel,
    EvaluationRunModel,
    EvaluationScoreModel,
    RunStatus,
)
from hecate.ops.evaluation.engine import EvaluationEngine
from hecate.ops.evaluation.evaluator import Evaluator
from hecate.ops.evaluation.types import AnswerSource, EvalInput, EvalOutput, Score


class _StaticEvaluator(Evaluator):
    """Deterministic evaluator returning a configured score."""

    def __init__(self, value: float = 0.9) -> None:
        self._value = value

    @property
    def name(self) -> str:
        return "static"

    @property
    def description(self) -> str:
        return "returns a fixed score"

    async def evaluate(self, input: EvalInput) -> EvalOutput:  # noqa: A002 — ABC signature
        return EvalOutput(scores=[Score(metric_name="static", value=self._value, source="deterministic")])


async def _seed_dataset(db_session, items: list[dict]) -> uuid.UUID:
    dataset_id = uuid.uuid4()
    for item in items:
        db_session.add(EvaluationItemModel(dataset_id=dataset_id, **item))
    await db_session.flush()
    return dataset_id


class TestAgentAnswerSource:
    async def test_agent_invocation_scores_generated_answer(self, db_session) -> None:
        dataset_id = await _seed_dataset(db_session, [{"query": "what is X?", "expected_answer": "X"}])
        agent_id = uuid.uuid4()
        captured: dict = {}

        async def _fake_agent_query(query: str, invoked_agent_id: uuid.UUID) -> str:
            captured["query"] = query
            captured["agent_id"] = invoked_agent_id
            return "X is a thing"

        engine = EvaluationEngine(db_session)
        original = engine._generate_answer_via_agent
        engine._generate_answer_via_agent = _fake_agent_query  # type: ignore[method-assign]
        try:
            result = await engine.run(
                [_StaticEvaluator()],
                dataset_id,
                answer_source=AnswerSource.AGENT,
                agent_id=agent_id,
            )
        finally:
            engine._generate_answer_via_agent = original  # type: ignore[method-assign]

        assert captured == {"query": "what is X?", "agent_id": agent_id}
        assert result.total_items == 1
        assert result.metric_averages["static"] == pytest.approx(0.9)

    async def test_agent_failure_isolated_to_item(self, db_session) -> None:
        dataset_id = await _seed_dataset(
            db_session,
            [{"query": "boom"}, {"query": "fine", "generated_answer": "prefilled"}],
        )
        agent_id = uuid.uuid4()

        async def _exploding_agent(query: str, invoked_agent_id: uuid.UUID) -> str:
            raise RuntimeError("agent down")

        engine = EvaluationEngine(db_session)
        original = engine._generate_answer_via_agent
        engine._generate_answer_via_agent = _exploding_agent  # type: ignore[method-assign]
        try:
            result = await engine.run(
                [_StaticEvaluator()],
                dataset_id,
                answer_source=AnswerSource.AGENT,
                agent_id=agent_id,
            )
        finally:
            engine._generate_answer_via_agent = original  # type: ignore[method-assign]

        assert result.total_items == 2
        # The failing item got an error score; the sibling still scored 0.9.
        assert result.metric_averages["static"] == pytest.approx(0.9)
        failed = [scores for scores in result.item_scores.values() if any(s.value == -1.0 for s in scores)]
        assert len(failed) == 1
        assert "Agent invocation failed" in (failed[0][0].reasoning or "")

    async def test_agent_source_without_agent_id_raises(self, db_session) -> None:
        engine = EvaluationEngine(db_session)
        with pytest.raises(ValueError, match="agent_id"):
            await engine.run(
                [_StaticEvaluator()],
                uuid.uuid4(),
                answer_source=AnswerSource.AGENT,
            )


class TestRunSummary:
    async def test_threshold_summary_on_reused_run(self, db_session) -> None:
        dataset_id = await _seed_dataset(
            db_session,
            [
                {"query": "q1", "generated_answer": "a1"},
                {"query": "q2", "generated_answer": "a2"},
            ],
        )
        run = EvaluationRunModel(dataset_id=dataset_id, status=RunStatus.PENDING.value)
        db_session.add(run)
        await db_session.flush()

        engine = EvaluationEngine(db_session)
        result = await engine.run(
            [_StaticEvaluator(value=0.9)],
            dataset_id,
            run=run,
            summary_config={"threshold": 0.5},
        )

        assert result.run_id == run.id
        assert run.summary["total_items"] == 2
        assert run.summary["passed_items"] == 2
        assert run.summary["failed_items"] == 0
        assert run.summary["pass_rate"] == pytest.approx(1.0)
        assert run.summary["regressions"] == []

    async def test_below_threshold_item_fails(self, db_session) -> None:
        dataset_id = await _seed_dataset(
            db_session,
            [
                {"query": "q1", "generated_answer": "a1"},
                {"query": "q2", "generated_answer": "a2"},
            ],
        )

        engine = EvaluationEngine(db_session)
        await engine.run(
            [_StaticEvaluator(value=0.3)],
            dataset_id,
            summary_config={"threshold": 0.5},
        )
        row = (
            await db_session.execute(select(EvaluationRunModel).where(EvaluationRunModel.dataset_id == dataset_id))
        ).scalar_one()
        assert row.summary["passed_items"] == 0
        assert row.summary["failed_items"] == 2
        assert row.summary["pass_rate"] == pytest.approx(0.0)

    async def test_baseline_regression_flagged(self, db_session) -> None:
        dataset_id = await _seed_dataset(db_session, [{"query": "q", "generated_answer": "a"}])
        baseline = EvaluationRunModel(dataset_id=dataset_id, status=RunStatus.COMPLETED.value)
        db_session.add(baseline)
        await db_session.flush()
        db_session.add(
            EvaluationScoreModel(
                run_id=baseline.id,
                item_id=uuid.uuid4(),
                metric_name="static",
                value=0.9,
                source="deterministic",
            )
        )
        await db_session.flush()

        engine = EvaluationEngine(db_session)
        result = await engine.run(
            [_StaticEvaluator(value=0.5)],
            dataset_id,
            summary_config={"baseline_run_id": str(baseline.id), "regression_threshold": 0.05},
        )

        run = (
            await db_session.execute(select(EvaluationRunModel).where(EvaluationRunModel.id == result.run_id))
        ).scalar_one()
        assert run.summary["passed_items"] is None  # no threshold configured
        assert len(run.summary["regressions"]) == 1
        reg = run.summary["regressions"][0]
        assert reg["metric_name"] == "static"
        assert reg["baseline"] == pytest.approx(0.9)
        assert reg["candidate"] == pytest.approx(0.5)


class TestOfflineTaskRunner:
    async def test_resolve_and_summary_config_helpers(self, db_session, monkeypatch: pytest.MonkeyPatch) -> None:
        """Runner helpers resolve evaluators via the registry and derive the
        engine summary config from the task config."""
        from hecate.ops.evaluation.tasks.runner import OfflineTaskRunner

        monkeypatch.setattr(
            "hecate.ops.evaluation.tasks.runner.get_evaluator_class",
            lambda name: _StaticEvaluator if name == "static" else None,
        )

        runner = OfflineTaskRunner(db_session)
        task = SimpleNamespace(
            id=uuid.uuid4(),
            evaluator_configs=["static", "missing"],
            config={"threshold": 0.7, "baseline_run_id": str(uuid.uuid4())},
        )
        evaluators = runner._resolve_evaluators(task)
        assert len(evaluators) == 1  # "missing" skipped with a warning

        summary_config = runner._summary_config(task)
        assert summary_config == {
            "threshold": 0.7,
            "baseline_run_id": task.config["baseline_run_id"],
            "regression_threshold": 0.05,
        }

        task_no_summary = SimpleNamespace(evaluator_configs=["static"], config={})
        assert runner._summary_config(task_no_summary) is None

    async def test_execute_completes_run(self, db_session, monkeypatch: pytest.MonkeyPatch) -> None:
        """Drive the runner's execution path synchronously: the run row is
        marked running, the engine scores items into the reused row, and the
        summary lands on the run."""
        from hecate.ops.evaluation.tasks.runner import OfflineTaskRunner

        dataset_id = await _seed_dataset(db_session, [{"query": "q", "generated_answer": "a"}])
        task = SimpleNamespace(
            id=uuid.uuid4(),
            evaluator_configs=["static"],
            config={"dataset_id": str(dataset_id), "answer_source": "manual", "threshold": 0.5},
        )
        run = EvaluationRunModel(
            dataset_id=dataset_id,
            task_id=task.id,
            evaluator_configs=["static"],
            status=RunStatus.PENDING.value,
        )
        db_session.add(run)
        await db_session.flush()

        monkeypatch.setattr(
            "hecate.ops.evaluation.tasks.runner.get_evaluator_class",
            lambda name: _StaticEvaluator if name == "static" else None,
        )

        # The background path opens a fresh session via
        # async_session_factory; unit tests drive the same body against the
        # request session by invoking the engine portion directly.
        runner = OfflineTaskRunner(db_session)
        run.status = RunStatus.RUNNING.value
        engine = EvaluationEngine(db_session)
        await engine.run(
            runner._resolve_evaluators(task),
            dataset_id=run.dataset_id,
            answer_source=AnswerSource(task.config["answer_source"]),
            run=run,
            summary_config=runner._summary_config(task),
        )

        assert run.status == RunStatus.COMPLETED.value
        assert run.summary["passed_items"] == 1
        scores = (
            (await db_session.execute(select(EvaluationScoreModel).where(EvaluationScoreModel.run_id == run.id)))
            .scalars()
            .all()
        )
        assert len(scores) == 1
