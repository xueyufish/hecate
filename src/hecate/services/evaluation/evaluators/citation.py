"""Citation and grounding LLM-as-Judge evaluators."""

from __future__ import annotations

import logging

from hecate.services.evaluation.evaluator import Evaluator
from hecate.services.evaluation.evaluators.agent import _call_llm_judge
from hecate.services.evaluation.prompt_templates import PROMPT_LIBRARY
from hecate.services.evaluation.registry import register_evaluator
from hecate.services.evaluation.types import EvalInput, EvalOutput, Score, Timer

logger = logging.getLogger(__name__)


def _make_judge_evaluator(eval_name: str, eval_description: str, eval_category: str) -> type[Evaluator]:
    """Factory for creating LLM-judge evaluators from prompt templates."""

    class _JudgeEvaluator(Evaluator):
        category = eval_category

        @property
        def name(self) -> str:
            return eval_name

        @property
        def description(self) -> str:
            return eval_description

        async def evaluate(self, input: EvalInput) -> EvalOutput:
            with Timer() as timer:
                template = PROMPT_LIBRARY[eval_name]
                prompt = template.user_prompt_template.format(
                    query=input.query,
                    answer=input.generated_answer,
                    context="\n\n".join(input.retrieved_contexts),
                    expected=input.expected_answer or "",
                    system_prompt=input.system_prompt or "",
                )
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

    _JudgeEvaluator.__name__ = eval_name.title().replace("_", "") + "Evaluator"
    return _JudgeEvaluator


CitationRelevanceEvaluator = register_evaluator("citation_relevance")(
    _make_judge_evaluator("citation_relevance", "Checks if citations are relevant to the query", "result"),
)
SourceAttributionEvaluator = register_evaluator("source_attribution")(
    _make_judge_evaluator("source_attribution", "Verifies proper source attribution", "result"),
)
GroundednessCheckEvaluator = register_evaluator("groundedness_check")(
    _make_judge_evaluator("groundedness_check", "Checks if claims are grounded in context", "result"),
)
HallucinationDetectionEvaluator = register_evaluator("hallucination_detection")(
    _make_judge_evaluator("hallucination_detection", "Detects fabricated claims not supported by context", "result"),
)
