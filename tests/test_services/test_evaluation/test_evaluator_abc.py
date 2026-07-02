"""Tests for Evaluator abstract base class."""

from __future__ import annotations

import pytest

from hecate.services.evaluation.evaluator import Evaluator
from hecate.services.evaluation.types import EvalInput, EvalOutput, Score


class TestEvaluatorABC:
    """Test that Evaluator cannot be instantiated directly."""

    def test_cannot_instantiate_abstract(self) -> None:
        with pytest.raises(TypeError):
            Evaluator()  # type: ignore[abstract]

    def test_subclass_must_implement_evaluate(self) -> None:
        class IncompleteEvaluator(Evaluator):
            @property
            def name(self) -> str:
                return "incomplete"

            @property
            def description(self) -> str:
                return "missing evaluate"

        with pytest.raises(TypeError):
            IncompleteEvaluator()  # type: ignore[abstract]

    def test_complete_subclass_works(self) -> None:
        class DummyEvaluator(Evaluator):
            @property
            def name(self) -> str:
                return "dummy"

            @property
            def description(self) -> str:
                return "A dummy evaluator for testing"

            async def evaluate(self, input: EvalInput) -> EvalOutput:
                return EvalOutput(
                    scores=[Score(metric_name=self.name, value=1.0)],
                    duration_ms=10.0,
                )

        evaluator = DummyEvaluator()
        assert evaluator.name == "dummy"
        assert evaluator.description == "A dummy evaluator for testing"
        assert evaluator.llm_config is None

    def test_subclass_with_llm_config(self) -> None:
        from hecate.services.evaluation.types import LLMConfig

        class DummyEvaluator(Evaluator):
            @property
            def name(self) -> str:
                return "dummy"

            @property
            def description(self) -> str:
                return "test"

            async def evaluate(self, input: EvalInput) -> EvalOutput:
                return EvalOutput()

        config = LLMConfig(model="gpt-3.5-turbo")
        evaluator = DummyEvaluator(llm_config=config)
        assert evaluator.llm_config is not None
        assert evaluator.llm_config.model == "gpt-3.5-turbo"
