"""Tests for EvaluationEngine — batch execution, error isolation, score aggregation."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.models.evaluation import (
    EvaluationDatasetModel,
    EvaluationItemModel,
    EvaluationRunModel,
    EvaluationScoreModel,
)
from hecate.ops.evaluation.engine import EvaluationEngine
from hecate.ops.evaluation.evaluator import Evaluator
from hecate.ops.evaluation.types import AnswerSource, EvalInput, EvalOutput, Score


class AlwaysGoodEvaluator(Evaluator):
    """Returns score 1.0 for every input."""

    @property
    def name(self) -> str:
        return "always_good"

    @property
    def description(self) -> str:
        return "Always returns 1.0"

    async def evaluate(self, input: EvalInput) -> EvalOutput:
        return EvalOutput(
            scores=[Score(metric_name=self.name, value=1.0, source="deterministic")],
            duration_ms=5.0,
        )


class FailingEvaluator(Evaluator):
    """Always raises an exception."""

    @property
    def name(self) -> str:
        return "failing"

    @property
    def description(self) -> str:
        return "Always fails"

    async def evaluate(self, input: EvalInput) -> EvalOutput:
        msg = "Intentional failure"
        raise RuntimeError(msg)


class HalfScoreEvaluator(Evaluator):
    """Returns score 0.5 for every input."""

    @property
    def name(self) -> str:
        return "half_score"

    @property
    def description(self) -> str:
        return "Always returns 0.5"

    async def evaluate(self, input: EvalInput) -> EvalOutput:
        return EvalOutput(
            scores=[Score(metric_name=self.name, value=0.5, source="deterministic")],
            duration_ms=3.0,
        )


@pytest.mark.parametrize(
    ("answer_source", "max_in_flight"),
    [
        (AnswerSource.AUTO, 1),
        (AnswerSource.PIPELINE, 1),
        (AnswerSource.AGENT, 1),
        (AnswerSource.WORKFLOW, 1),
        (AnswerSource.WORKFLOW, 2),
    ],
)
async def test_generation_failure_persists_each_metric_and_keeps_siblings(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    answer_source: AnswerSource,
    max_in_flight: int,
) -> None:
    """Serial and concurrent generation errors persist alongside successful siblings."""
    workspace_id = uuid.uuid4()
    dataset = EvaluationDatasetModel(name="generation-failure", workspace_id=workspace_id)
    db_session.add(dataset)
    await db_session.flush()
    items = [
        EvaluationItemModel(dataset_id=dataset.id, workspace_id=workspace_id, query=query)
        for query in ("broken", "healthy-first", "healthy-second")
    ]
    db_session.add_all(items)
    run = EvaluationRunModel(
        dataset_id=dataset.id,
        workspace_id=workspace_id,
        evaluator_configs=["always_good", "half_score"],
    )
    db_session.add(run)
    await db_session.flush()
    generation_calls: list[str] = []
    evaluation_calls: list[str] = []

    async def generate(query: str) -> str:
        generation_calls.append(query)
        if query == "broken":
            raise RuntimeError("stub generation failed")
        return "generated answer"

    async def generate_pipeline(self: EvaluationEngine, query: str, context: list[str]) -> str:
        return await generate(query)

    async def generate_agent(
        self: EvaluationEngine,
        query: str,
        agent_id: uuid.UUID,
        agent_definition: object = None,
        rollout_capture: list[dict] | None = None,
    ) -> str:
        return await generate(query)

    async def generate_workflow(self: EvaluationEngine, *, query: str, **kwargs: object) -> tuple[str, list[dict]]:
        return await generate(query), []

    async def ensure_workflow_task(self: EvaluationEngine, *args: object) -> None:
        return None

    original_evaluate = AlwaysGoodEvaluator.evaluate

    async def evaluate(self: AlwaysGoodEvaluator, input: EvalInput) -> EvalOutput:
        evaluation_calls.append(input.query)
        assert input.generated_answer == "generated answer"
        return await original_evaluate(self, input)

    monkeypatch.setattr(EvaluationEngine, "_generate_answer_via_pipeline", generate_pipeline)
    monkeypatch.setattr(EvaluationEngine, "_generate_answer_via_agent", generate_agent)
    monkeypatch.setattr(EvaluationEngine, "_generate_answer_via_workflow", generate_workflow)
    monkeypatch.setattr(EvaluationEngine, "_ensure_workflow_task", ensure_workflow_task)
    monkeypatch.setattr(AlwaysGoodEvaluator, "evaluate", evaluate)
    result = await EvaluationEngine(db_session).run(
        [AlwaysGoodEvaluator(), HalfScoreEvaluator()],
        dataset.id,
        answer_source=answer_source,
        agent_id=uuid.uuid4() if answer_source == AnswerSource.AGENT else None,
        workflow_id=uuid.uuid4() if answer_source == AnswerSource.WORKFLOW else None,
        workflow_version=1,
        max_in_flight=max_in_flight,
        run=run,
    )
    await db_session.commit()

    assert sorted(generation_calls) == ["broken", "healthy-first", "healthy-second"]
    assert sorted(evaluation_calls) == ["healthy-first", "healthy-second"]
    assert result.total_items == 3
    assert result.metric_averages == {"always_good": 1.0, "half_score": 0.5}
    scores = (await db_session.execute(select(EvaluationScoreModel.__table__))).mappings().all()
    assert len(scores) == 6
    assert all(score["workspace_id"] == workspace_id and score["run_id"] == run.id for score in scores)
    persisted = {
        (str(score["item_id"]), score["metric_name"], score["value"], score["reasoning"], score["source"])
        for score in scores
    }
    returned = {
        (item_id, score.metric_name, score.value, score.reasoning, score.source)
        for item_id, item_scores in result.item_scores.items()
        for score in item_scores
    }
    assert persisted == returned and len(persisted) == 6
    failed_scores = [score for score in scores if score["item_id"] == items[0].id]
    assert len(failed_scores) == 2
    assert all(score["value"] == -1.0 and score["source"] == "llm_judge" for score in failed_scores)
    failure_prefix = {
        AnswerSource.AGENT: "Agent invocation failed",
        AnswerSource.WORKFLOW: "Workflow execution failed",
    }.get(answer_source, "Pipeline answer generation failed")
    assert all(score["reasoning"] == f"{failure_prefix}: stub generation failed" for score in failed_scores)
    persisted_run = (await db_session.execute(select(EvaluationRunModel.__table__))).mappings().one()
    assert persisted_run["status"] == "completed" and persisted_run["completed_at"] is not None


async def _create_test_dataset(db: object, n_items: int = 3) -> uuid.UUID:
    """Create a dataset with n test items."""
    ds = EvaluationDatasetModel(name="test-dataset")
    db.add(ds)  # type: ignore[attr-defined]
    await db.flush()  # type: ignore[attr-defined]

    for i in range(n_items):
        item = EvaluationItemModel(
            dataset_id=ds.id,
            query=f"query {i}",
            expected_answer=f"answer {i}",
            context=[f"context {i}"],
        )
        db.add(item)  # type: ignore[attr-defined]
    await db.flush()  # type: ignore[attr-defined]
    return ds.id


class TestEvaluationEngine:
    async def test_basic_run(self, db_session: object) -> None:
        ds_id = await _create_test_dataset(db_session, n_items=3)
        engine = EvaluationEngine(db_session)  # type: ignore[arg-type]
        evaluators = [AlwaysGoodEvaluator()]

        result = await engine.run(evaluators, ds_id)

        assert result.dataset_id == ds_id
        assert result.total_items == 3
        assert result.total_duration_ms > 0
        assert "always_good" in result.metric_averages
        assert result.metric_averages["always_good"] == 1.0
        assert len(result.item_scores) == 3

    async def test_multiple_evaluators(self, db_session: object) -> None:
        ds_id = await _create_test_dataset(db_session, n_items=2)
        engine = EvaluationEngine(db_session)  # type: ignore[arg-type]
        evaluators = [AlwaysGoodEvaluator(), HalfScoreEvaluator()]

        result = await engine.run(evaluators, ds_id)

        assert result.total_items == 2
        assert "always_good" in result.metric_averages
        assert "half_score" in result.metric_averages
        assert result.metric_averages["always_good"] == 1.0
        assert result.metric_averages["half_score"] == 0.5
        # Each item should have 2 scores
        for scores in result.item_scores.values():
            assert len(scores) == 2

    async def test_error_isolation(self, db_session: object) -> None:
        ds_id = await _create_test_dataset(db_session, n_items=2)
        engine = EvaluationEngine(db_session)  # type: ignore[arg-type]
        evaluators = [AlwaysGoodEvaluator(), FailingEvaluator()]

        result = await engine.run(evaluators, ds_id)

        assert result.total_items == 2
        # always_good should still have valid averages
        assert "always_good" in result.metric_averages
        assert result.metric_averages["always_good"] == 1.0
        # failing evaluator's scores should be -1.0 (excluded from averages)
        for scores in result.item_scores.values():
            failing_scores = [s for s in scores if s.metric_name == "failing"]
            assert len(failing_scores) == 1
            assert failing_scores[0].value == -1.0
            assert "Evaluator error" in (failing_scores[0].reasoning or "")

    async def test_empty_dataset(self, db_session: object) -> None:
        ds_id = await _create_test_dataset(db_session, n_items=0)
        engine = EvaluationEngine(db_session)  # type: ignore[arg-type]

        result = await engine.run([AlwaysGoodEvaluator()], ds_id)
        assert result.total_items == 0
        assert result.item_scores == {}
        assert result.metric_averages == {}

    async def test_answer_source_manual(self, db_session: object) -> None:
        """Manual answer source uses item.generated_answer."""
        ds_id = await _create_test_dataset(db_session, n_items=1)
        stmt = select(EvaluationItemModel).where(EvaluationItemModel.dataset_id == ds_id)
        result = await db_session.execute(stmt)  # type: ignore[union-attr]
        item = result.scalars().first()
        item.generated_answer = "pre-generated answer"
        await db_session.flush()  # type: ignore[union-attr]

        engine = EvaluationEngine(db_session)  # type: ignore[arg-type]
        run_result = await engine.run([AlwaysGoodEvaluator()], ds_id, answer_source=AnswerSource.MANUAL)
        assert run_result.total_items == 1
        assert "always_good" in run_result.metric_averages

    async def test_answer_source_auto_with_existing_answer(self, db_session: object) -> None:
        """AUTO source uses pre-populated answer when available."""
        ds_id = await _create_test_dataset(db_session, n_items=1)
        stmt = select(EvaluationItemModel).where(EvaluationItemModel.dataset_id == ds_id)
        result = await db_session.execute(stmt)  # type: ignore[union-attr]
        item = result.scalars().first()
        item.generated_answer = "existing answer"
        await db_session.flush()  # type: ignore[union-attr]

        engine = EvaluationEngine(db_session)  # type: ignore[arg-type]
        run_result = await engine.run([AlwaysGoodEvaluator()], ds_id, answer_source=AnswerSource.AUTO)
        assert run_result.total_items == 1
        assert run_result.metric_averages["always_good"] == 1.0

    async def test_answer_source_auto_no_existing_answer(self, db_session: object) -> None:
        """AUTO source falls back to pipeline when no pre-populated answer."""
        ds_id = await _create_test_dataset(db_session, n_items=1)
        engine = EvaluationEngine(db_session)  # type: ignore[arg-type]

        run_result = await engine.run([AlwaysGoodEvaluator()], ds_id, answer_source=AnswerSource.AUTO)
        assert run_result.total_items == 1
        assert "always_good" in run_result.metric_averages

    async def test_answer_source_pipeline_fallback(self, db_session: object) -> None:
        """PIPELINE source falls back gracefully when LLM unavailable."""
        ds_id = await _create_test_dataset(db_session, n_items=1)
        engine = EvaluationEngine(db_session)  # type: ignore[arg-type]

        run_result = await engine.run([AlwaysGoodEvaluator()], ds_id, answer_source=AnswerSource.PIPELINE)
        assert run_result.total_items == 1
