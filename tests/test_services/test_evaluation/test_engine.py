"""Tests for EvaluationEngine — batch execution, error isolation, score aggregation."""

from __future__ import annotations

import uuid

from hecate.models.evaluation import EvaluationDatasetModel, EvaluationItemModel
from hecate.services.evaluation.engine import EvaluationEngine
from hecate.services.evaluation.evaluator import Evaluator
from hecate.services.evaluation.types import EvalInput, EvalOutput, Score


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
