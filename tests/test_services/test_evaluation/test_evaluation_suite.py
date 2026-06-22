"""Tests for evaluation suite — evaluators, registry, and regression service."""

from __future__ import annotations

import uuid

import pytest

import hecate.services.evaluation.evaluators  # noqa: F401 — trigger registration
from hecate.services.evaluation.evaluator import Evaluator
from hecate.services.evaluation.registry import (
    get_evaluator,
    list_evaluator_names,
    list_evaluators,
    register_evaluator,
)
from hecate.services.evaluation.types import EvalInput


class TestRegistry:
    """Test the evaluator registry functions."""

    def test_list_evaluators_returns_dict(self) -> None:
        result = list_evaluators()
        assert isinstance(result, dict)
        assert len(result) > 0

    def test_list_evaluators_by_category(self) -> None:
        result_evaluators = list_evaluators(category="result")
        assert all(getattr(cls, "category", "generic") == "result" for cls in result_evaluators.values())

    def test_list_evaluator_names_sorted(self) -> None:
        names = list_evaluator_names()
        assert names == sorted(names)

    def test_get_evaluator_existing(self) -> None:
        cls = get_evaluator("exact_match")
        assert cls is not None
        assert issubclass(cls, Evaluator)

    def test_get_evaluator_nonexistent(self) -> None:
        cls = get_evaluator("nonexistent_evaluator_xyz")
        assert cls is None

    def test_register_custom_evaluator(self) -> None:
        @register_evaluator("_test_custom_eval")
        class _Custom(Evaluator):
            category = "generic"

            @property
            def name(self) -> str:
                return "_test_custom_eval"

            @property
            def description(self) -> str:
                return "Test evaluator"

            async def evaluate(self, input: EvalInput):
                pass

        assert get_evaluator("_test_custom_eval") is _Custom


class TestFormatEvaluators:
    """Test deterministic format evaluators."""

    @pytest.fixture
    def make_input(self):
        def _make(query: str = "test", answer: str = "", expected: str | None = None, **kwargs):
            return EvalInput(
                query=query,
                generated_answer=answer,
                expected_answer=expected,
                metadata=kwargs,
            )

        return _make

    async def test_exact_match_pass(self, make_input) -> None:
        cls = get_evaluator("exact_match")
        evaluator = cls()
        result = await evaluator.evaluate(make_input(answer="Paris", expected="Paris"))
        assert len(result.scores) == 1
        assert result.scores[0].value == 1.0
        assert result.scores[0].source == "deterministic"

    async def test_exact_match_fail(self, make_input) -> None:
        cls = get_evaluator("exact_match")
        evaluator = cls()
        result = await evaluator.evaluate(make_input(answer="London", expected="Paris"))
        assert result.scores[0].value == 0.0

    async def test_exact_match_case_insensitive(self, make_input) -> None:
        cls = get_evaluator("exact_match")
        evaluator = cls()
        result = await evaluator.evaluate(make_input(answer="  paris  ", expected="Paris"))
        assert result.scores[0].value == 1.0

    async def test_exact_match_no_expected(self, make_input) -> None:
        cls = get_evaluator("exact_match")
        evaluator = cls()
        result = await evaluator.evaluate(make_input(answer="test"))
        assert result.scores[0].value == -1.0

    async def test_contains_found(self, make_input) -> None:
        cls = get_evaluator("contains")
        evaluator = cls()
        result = await evaluator.evaluate(make_input(answer="RAG stands for Retrieval", expected_substring="Retrieval"))
        assert result.scores[0].value == 1.0

    async def test_contains_not_found(self, make_input) -> None:
        cls = get_evaluator("contains")
        evaluator = cls()
        result = await evaluator.evaluate(make_input(answer="Hello world", expected_substring="RAG"))
        assert result.scores[0].value == 0.0

    async def test_contains_any_found(self, make_input) -> None:
        cls = get_evaluator("contains_any")
        evaluator = cls()
        result = await evaluator.evaluate(make_input(answer="Hello RAG", expected_substrings=["foo", "RAG", "bar"]))
        assert result.scores[0].value == 1.0

    async def test_contains_any_not_found(self, make_input) -> None:
        cls = get_evaluator("contains_any")
        evaluator = cls()
        result = await evaluator.evaluate(make_input(answer="Hello world", expected_substrings=["foo", "bar"]))
        assert result.scores[0].value == 0.0

    async def test_regex_match_found(self, make_input) -> None:
        cls = get_evaluator("regex_match")
        evaluator = cls()
        result = await evaluator.evaluate(make_input(answer="Error code: E-1234", regex_pattern=r"E-\d{4}"))
        assert result.scores[0].value == 1.0

    async def test_regex_match_not_found(self, make_input) -> None:
        cls = get_evaluator("regex_match")
        evaluator = cls()
        result = await evaluator.evaluate(make_input(answer="No error here", regex_pattern=r"E-\d{4}"))
        assert result.scores[0].value == 0.0

    async def test_is_json_valid(self, make_input) -> None:
        cls = get_evaluator("is_json")
        evaluator = cls()
        result = await evaluator.evaluate(make_input(answer='{"key": "value"}'))
        assert result.scores[0].value == 1.0

    async def test_is_json_invalid(self, make_input) -> None:
        cls = get_evaluator("is_json")
        evaluator = cls()
        result = await evaluator.evaluate(make_input(answer="not json"))
        assert result.scores[0].value == 0.0

    async def test_format_check_valid(self, make_input) -> None:
        cls = get_evaluator("format_check")
        evaluator = cls()
        result = await evaluator.evaluate(
            make_input(
                answer='{"name": "test", "value": 42}',
                schema={"required": ["name", "value"]},
            )
        )
        assert result.scores[0].value == 1.0

    async def test_format_check_missing_key(self, make_input) -> None:
        cls = get_evaluator("format_check")
        evaluator = cls()
        result = await evaluator.evaluate(
            make_input(
                answer='{"name": "test"}',
                schema={"required": ["name", "value"]},
            )
        )
        assert result.scores[0].value == 0.0

    async def test_bleu_perfect_match(self, make_input) -> None:
        cls = get_evaluator("bleu_score")
        evaluator = cls()
        result = await evaluator.evaluate(
            make_input(answer="the cat sat on the mat", expected="the cat sat on the mat")
        )
        assert result.scores[0].value == pytest.approx(1.0, abs=0.01)

    async def test_bleu_no_expected(self, make_input) -> None:
        cls = get_evaluator("bleu_score")
        evaluator = cls()
        result = await evaluator.evaluate(make_input(answer="test"))
        assert result.scores[0].value == -1.0

    async def test_rouge_perfect_match(self, make_input) -> None:
        cls = get_evaluator("rouge_score")
        evaluator = cls()
        result = await evaluator.evaluate(
            make_input(answer="the cat sat on the mat", expected="the cat sat on the mat")
        )
        assert result.scores[0].value == pytest.approx(1.0, abs=0.01)

    async def test_f1_partial_overlap(self, make_input) -> None:
        cls = get_evaluator("f1_score")
        evaluator = cls()
        result = await evaluator.evaluate(
            make_input(
                answer="RAG uses retrieval and generation",
                expected="RAG combines retrieval with generation",
            )
        )
        assert 0.0 < result.scores[0].value < 1.0


class TestProgrammaticEvaluators:
    """Test programmatic code execution evaluators."""

    async def test_python_code_eval_with_func(self) -> None:
        from hecate.services.evaluation.evaluators.programmatic import PythonCodeEvaluator

        def my_func(inp: EvalInput) -> float:
            return 1.0 if "RAG" in inp.generated_answer else 0.0

        evaluator = PythonCodeEvaluator(func=my_func)
        result = await evaluator.evaluate(EvalInput(query="q", generated_answer="RAG is great"))
        assert result.scores[0].value == 1.0
        assert result.scores[0].source == "deterministic"

    async def test_python_code_eval_no_func(self) -> None:
        from hecate.services.evaluation.evaluators.programmatic import PythonCodeEvaluator

        evaluator = PythonCodeEvaluator()
        result = await evaluator.evaluate(EvalInput(query="q", generated_answer="test"))
        assert result.scores[0].value == -1.0

    async def test_custom_callable_with_func(self) -> None:
        from hecate.services.evaluation.evaluators.programmatic import CustomCallableEvaluator

        async def my_async_func(inp: EvalInput) -> float:
            return 0.5

        evaluator = CustomCallableEvaluator(callable=my_async_func)
        result = await evaluator.evaluate(EvalInput(query="q", generated_answer="test"))
        assert result.scores[0].value == 0.5


class TestJudgePromptTemplate:
    """Test JudgePromptTemplate construction."""

    def test_binary_template(self) -> None:
        from hecate.services.evaluation.prompt_templates import JudgePromptTemplate

        template = JudgePromptTemplate(
            scoring_scale="binary",
            system_prompt="You are a judge.",
            user_prompt_template="Evaluate: {answer}",
        )
        assert template.scoring_scale == "binary"
        assert "{answer}" in template.user_prompt_template

    def test_5_point_template(self) -> None:
        from hecate.services.evaluation.prompt_templates import FIVE_POINT_RUBRIC, JudgePromptTemplate

        template = JudgePromptTemplate(
            scoring_scale="5_point",
            system_prompt="You are a judge.",
            user_prompt_template="Evaluate: {answer}",
            scoring_rubric=FIVE_POINT_RUBRIC,
        )
        assert len(template.scoring_rubric) == 5
        assert 1.0 in template.scoring_rubric
        assert 0.0 in template.scoring_rubric

    def test_prompt_library_has_templates(self) -> None:
        from hecate.services.evaluation.prompt_templates import PROMPT_LIBRARY

        assert "toxicity_detection" in PROMPT_LIBRARY
        assert "safety_harmlessness" in PROMPT_LIBRARY
        assert "hallucination_detection" in PROMPT_LIBRARY
        assert "tool_selection_accuracy" in PROMPT_LIBRARY


class TestDatasetServiceVersioning:
    """Test dataset versioning and lock/unlock via service."""

    async def test_create_dataset_with_version(self, db_session) -> None:
        from hecate.services.evaluation.dataset_service import EvaluationDatasetService

        svc = EvaluationDatasetService(db_session)
        ds = await svc.create_dataset(name="test-v2", version="v2.0", default_threshold=0.8)
        assert ds.version == "v2.0"
        assert ds.default_threshold == 0.8
        assert ds.is_locked is False

    async def test_lock_unlock_dataset(self, db_session) -> None:
        from hecate.services.evaluation.dataset_service import EvaluationDatasetService

        svc = EvaluationDatasetService(db_session)
        ds = await svc.create_dataset(name="lock-test")
        assert ds.is_locked is False

        locked = await svc.lock_dataset(ds.id)
        assert locked.is_locked is True

        unlocked = await svc.unlock_dataset(ds.id)
        assert unlocked.is_locked is False

    async def test_add_items_to_locked_dataset_raises(self, db_session) -> None:
        from hecate.services.evaluation.dataset_service import EvaluationDatasetService

        svc = EvaluationDatasetService(db_session)
        ds = await svc.create_dataset(name="locked-ds")
        await svc.lock_dataset(ds.id)

        with pytest.raises(ValueError, match="locked"):
            await svc.add_items(ds.id, [{"query": "test"}])

    async def test_add_items_with_assertions_and_tags(self, db_session) -> None:
        from hecate.services.evaluation.dataset_service import EvaluationDatasetService

        svc = EvaluationDatasetService(db_session)
        ds = await svc.create_dataset(name="assertions-test")
        count = await svc.add_items(
            ds.id,
            [
                {
                    "query": "What is RAG?",
                    "assertions": [{"type": "faithfulness", "threshold": 0.85}],
                    "tags": ["smoke", "regression"],
                }
            ],
        )
        assert count == 1

    async def test_set_baseline_run(self, db_session) -> None:
        from hecate.services.evaluation.dataset_service import EvaluationDatasetService

        svc = EvaluationDatasetService(db_session)
        ds = await svc.create_dataset(name="baseline-test")
        run_id = uuid.uuid4()
        updated = await svc.set_baseline_run(ds.id, run_id)
        assert updated.baseline_run_id == run_id
