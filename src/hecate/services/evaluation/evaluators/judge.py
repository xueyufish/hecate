"""Generic LLM-as-Judge evaluators."""

from __future__ import annotations

import logging
from typing import Any

from hecate.services.evaluation.evaluator import Evaluator
from hecate.services.evaluation.evaluators.agent import _call_llm_judge
from hecate.services.evaluation.prompt_templates import PROMPT_LIBRARY
from hecate.services.evaluation.registry import register_evaluator
from hecate.services.evaluation.types import EvalInput, EvalOutput, Score, Timer

logger = logging.getLogger(__name__)


def _make_generic_evaluator(eval_name: str, eval_description: str) -> type[Evaluator]:
    """Factory for creating generic LLM-judge evaluators."""

    class _GenericEvaluator(Evaluator):
        category = "generic"

        @property
        def name(self) -> str:
            return eval_name

        @property
        def description(self) -> str:
            return eval_description

        async def evaluate(self, input: EvalInput) -> EvalOutput:
            with Timer() as timer:
                template = PROMPT_LIBRARY[eval_name]
                kwargs: dict[str, Any] = dict(
                    query=input.query,
                    answer=input.generated_answer,
                    context="\n\n".join(input.retrieved_contexts),
                    expected=input.expected_answer or "",
                    system_prompt=input.system_prompt or "",
                )
                if eval_name == "llm_rubric":
                    rubric = input.metadata.get("rubric", "Score based on overall quality.")
                    kwargs["answer"] = f"Rubric: {rubric}\n\nResponse: {input.generated_answer}"
                prompt = template.user_prompt_template.format(**kwargs)
                try:
                    result = await _call_llm_judge(prompt, self.llm_config)
                    score = Score(
                        metric_name=eval_name,
                        value=float(result["score"]),
                        reasoning=result.get("reasoning"),
                        source="llm_judge",
                    )
                except Exception as e:
                    logger.error("%s failed: %s", eval_name, e)
                    score = Score(
                        metric_name=eval_name,
                        value=-1.0,
                        reasoning=f"LLM judge error: {e}",
                        source="llm_judge",
                    )
            return EvalOutput(scores=[score], duration_ms=timer.elapsed_ms)

    _GenericEvaluator.__name__ = eval_name.title().replace("_", "") + "Evaluator"
    return _GenericEvaluator


SemanticSimilarityEvaluator = register_evaluator("semantic_similarity")(
    _make_generic_evaluator("semantic_similarity", "Measures semantic equivalence between answer and expected"),
)
RubricScoringEvaluator = register_evaluator("rubric_scoring")(
    _make_generic_evaluator("rubric_scoring", "Generic rubric-based scoring"),
)
FactualityCheckEvaluator = register_evaluator("factuality_check")(
    _make_generic_evaluator("factuality_check", "Checks factual accuracy of claims"),
)
LLMRubricEvaluator = register_evaluator("llm_rubric")(
    _make_generic_evaluator("llm_rubric", "Accepts custom rubric string for domain-specific evaluation"),
)
