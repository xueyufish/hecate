"""Tests for EvaluatorABC and BuiltinEvaluator."""

from __future__ import annotations

import pytest

from hecate.plugin.manifest import PluginManifest
from hecate.plugin.registry import PluginRegistry
from hecate.plugin.spi.evaluator import EvaluatorABC
from hecate.services.evaluation.evaluator import BuiltinEvaluator, Evaluator
from hecate.services.evaluation.types import EvalInput, EvalOutput


class TestEvaluatorABC:
    """Tests for EvaluatorABC abstract interface."""

    def test_cannot_instantiate_directly(self) -> None:
        """EvaluatorABC cannot be instantiated directly."""
        with pytest.raises(TypeError):
            EvaluatorABC()  # type: ignore[abstract]

    def test_subclass_must_implement_name(self) -> None:
        """Subclass without name property cannot be instantiated."""

        class IncompleteEvaluator(EvaluatorABC):
            @property
            def description(self) -> str:
                return "test"

            async def evaluate(self, input: EvalInput) -> EvalOutput:
                return EvalOutput(scores=[])

        with pytest.raises(TypeError):
            IncompleteEvaluator()  # type: ignore[abstract]

    def test_subclass_must_implement_description(self) -> None:
        """Subclass without description property cannot be instantiated."""

        class IncompleteEvaluator(EvaluatorABC):
            @property
            def name(self) -> str:
                return "test"

            async def evaluate(self, input: EvalInput) -> EvalOutput:
                return EvalOutput(scores=[])

        with pytest.raises(TypeError):
            IncompleteEvaluator()  # type: ignore[abstract]

    def test_subclass_must_implement_evaluate(self) -> None:
        """Subclass without evaluate method cannot be instantiated."""

        class IncompleteEvaluator(EvaluatorABC):
            @property
            def name(self) -> str:
                return "test"

            @property
            def description(self) -> str:
                return "test"

        with pytest.raises(TypeError):
            IncompleteEvaluator()  # type: ignore[abstract]

    def test_complete_subclass_can_be_instantiated(self) -> None:
        """Complete subclass with all abstract members can be instantiated."""

        class CompleteEvaluator(EvaluatorABC):
            @property
            def name(self) -> str:
                return "test"

            @property
            def description(self) -> str:
                return "Test evaluator"

            async def evaluate(self, input: EvalInput) -> EvalOutput:
                return EvalOutput(scores=[])

        evaluator = CompleteEvaluator()
        assert evaluator.name == "test"
        assert evaluator.description == "Test evaluator"


class TestBuiltinEvaluator:
    """Tests for BuiltinEvaluator (backward compatibility)."""

    def test_evaluator_is_builtin_evaluator(self) -> None:
        """Evaluator alias points to BuiltinEvaluator."""
        assert Evaluator is BuiltinEvaluator

    def test_builtin_evaluator_inherits_from_evaluator_abc(self) -> None:
        """BuiltinEvaluator inherits from EvaluatorABC."""
        assert issubclass(BuiltinEvaluator, EvaluatorABC)

    def test_builtin_evaluator_accepts_llm_config(self) -> None:
        """BuiltinEvaluator accepts optional llm_config parameter."""

        class MyEvaluator(BuiltinEvaluator):
            @property
            def name(self) -> str:
                return "test"

            @property
            def description(self) -> str:
                return "test"

            async def evaluate(self, input: EvalInput) -> EvalOutput:
                return EvalOutput(scores=[])

        evaluator = MyEvaluator(llm_config=None)
        assert evaluator.llm_config is None


class TestEvaluatorRegistration:
    """Tests for registering evaluators with PluginRegistry."""

    def test_register_evaluator(self) -> None:
        """Evaluator can be registered with PluginRegistry."""

        class TestEvaluator(EvaluatorABC):
            @property
            def name(self) -> str:
                return "test_metric"

            @property
            def description(self) -> str:
                return "Test metric"

            async def evaluate(self, input: EvalInput) -> EvalOutput:
                return EvalOutput(scores=[])

        registry = PluginRegistry()
        evaluator = TestEvaluator()
        manifest = PluginManifest(
            type="evaluator",
            name=evaluator.name,
            version="1.0.0",
        )

        registry.register(manifest, evaluator)

        result = registry.get_by_name("evaluator", "test_metric")
        assert result is evaluator

    def test_list_evaluators(self) -> None:
        """Multiple evaluators can be listed by type."""
        registry = PluginRegistry()

        def _make_evaluator(eval_name: str) -> type[EvaluatorABC]:
            class TestEval(EvaluatorABC):
                @property
                def name(self) -> str:
                    return eval_name

                @property
                def description(self) -> str:
                    return f"{eval_name} desc"

                async def evaluate(self, input: EvalInput) -> EvalOutput:
                    return EvalOutput(scores=[])

            return TestEval

        for eval_name in ["metric_a", "metric_b"]:
            evaluator = _make_evaluator(eval_name)()
            manifest = PluginManifest(type="evaluator", name=eval_name, version="1.0.0")
            registry.register(manifest, evaluator)

        evaluators = registry.get_by_type("evaluator")
        assert "metric_a" in evaluators
        assert "metric_b" in evaluators
