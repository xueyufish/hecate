"""Tests for evaluation types: Score, EvalInput, EvalOutput, LLMConfig, EvaluationRunResult."""

from __future__ import annotations

import uuid

import pytest

from hecate.services.evaluation.types import (
    EvalInput,
    EvalOutput,
    EvaluationRunResult,
    LLMConfig,
    Score,
    Timer,
)


class TestScore:
    """Test Score dataclass validation."""

    def test_valid_score(self) -> None:
        score = Score(metric_name="test", value=0.85, reasoning="good", source="llm_judge")
        assert score.metric_name == "test"
        assert score.value == 0.85
        assert score.source == "llm_judge"

    def test_score_boundary_values(self) -> None:
        Score(metric_name="min", value=0.0)
        Score(metric_name="max", value=1.0)
        Score(metric_name="negative", value=-1.0)

    def test_score_value_too_high(self) -> None:
        with pytest.raises(ValueError, match=r"must be in \[-1.0, 1.0\]"):
            Score(metric_name="bad", value=1.5)

    def test_score_value_too_low(self) -> None:
        with pytest.raises(ValueError, match=r"must be in \[-1.0, 1.0\]"):
            Score(metric_name="bad", value=-2.0)

    def test_score_invalid_source(self) -> None:
        with pytest.raises(ValueError, match="Score source"):
            Score(metric_name="test", value=0.5, source="invalid")

    def test_score_all_valid_sources(self) -> None:
        for source in ("llm_judge", "deterministic", "human"):
            Score(metric_name="test", value=0.5, source=source)


class TestEvalInput:
    """Test EvalInput construction."""

    def test_minimal_input(self) -> None:
        inp = EvalInput(query="What is Python?")
        assert inp.query == "What is Python?"
        assert inp.retrieved_contexts == []
        assert inp.generated_answer == ""
        assert inp.expected_answer is None
        assert inp.tool_calls is None
        assert inp.metadata == {}

    def test_full_input(self) -> None:
        inp = EvalInput(
            query="What is Python?",
            retrieved_contexts=["Python is a language."],
            generated_answer="Python is a programming language.",
            expected_answer="A programming language.",
            tool_calls=[{"name": "search", "args": {"q": "Python"}}],
            metadata={"source": "test"},
        )
        assert len(inp.retrieved_contexts) == 1
        assert inp.expected_answer == "A programming language."


class TestEvalOutput:
    """Test EvalOutput construction."""

    def test_empty_output(self) -> None:
        out = EvalOutput()
        assert out.scores == []
        assert out.metadata == {}
        assert out.duration_ms == 0.0

    def test_output_with_scores(self) -> None:
        scores = [Score(metric_name="test", value=0.9)]
        out = EvalOutput(scores=scores, duration_ms=150.0)
        assert len(out.scores) == 1
        assert out.duration_ms == 150.0


class TestLLMConfig:
    """Test LLMConfig defaults."""

    def test_defaults(self) -> None:
        config = LLMConfig()
        assert config.model == "gpt-4o"
        assert config.temperature == 0.0
        assert config.api_base is None

    def test_custom_config(self) -> None:
        config = LLMConfig(model="gpt-3.5-turbo", temperature=0.7, api_base="http://localhost:8000")
        assert config.model == "gpt-3.5-turbo"
        assert config.temperature == 0.7


class TestEvaluationRunResult:
    """Test EvaluationRunResult construction."""

    def test_empty_result(self) -> None:
        result = EvaluationRunResult()
        assert result.run_id is not None
        assert result.dataset_id is None
        assert result.item_scores == {}
        assert result.metric_averages == {}
        assert result.total_items == 0

    def test_result_with_data(self) -> None:
        ds_id = uuid.uuid4()
        result = EvaluationRunResult(
            dataset_id=ds_id,
            item_scores={"item1": [Score(metric_name="test", value=0.8)]},
            metric_averages={"test": 0.8},
            total_items=1,
            total_duration_ms=200.0,
        )
        assert result.dataset_id == ds_id
        assert result.total_items == 1


class TestTimer:
    """Test Timer context manager."""

    def test_timer_records_elapsed(self) -> None:
        import time

        with Timer() as t:
            time.sleep(0.01)
        assert t.elapsed_ms > 0
        assert t.elapsed_ms < 1000
