"""Multi-turn interaction LLM-as-Judge evaluators."""

from __future__ import annotations

import json
import logging
from typing import Any

from hecate.services.evaluation.evaluator import Evaluator
from hecate.services.evaluation.evaluators.agent import _call_llm_judge
from hecate.services.evaluation.prompt_templates import PROMPT_LIBRARY
from hecate.services.evaluation.registry import register_evaluator
from hecate.services.evaluation.types import EvalInput, EvalOutput, Score, Timer

logger = logging.getLogger(__name__)


def _make_interaction_evaluator(eval_name: str, eval_description: str) -> type[Evaluator]:
    """Factory for creating interaction LLM-judge evaluators."""

    class _InteractionEvaluator(Evaluator):
        category = "interaction"

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
                    tool_calls=json.dumps(input.tool_calls or []),
                    conversation_history=json.dumps(input.conversation_history, indent=2),
                )
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

    _InteractionEvaluator.__name__ = eval_name.title().replace("_", "") + "Evaluator"
    return _InteractionEvaluator


MultiTurnSuccessEvaluator = register_evaluator("multi_turn_success")(
    _make_interaction_evaluator("multi_turn_success", "Evaluates task completion across multi-turn conversation"),
)
MultiTurnCoherenceEvaluator = register_evaluator("multi_turn_coherence")(
    _make_interaction_evaluator("multi_turn_coherence", "Checks consistency across conversation turns"),
)
ConversationQualityEvaluator = register_evaluator("conversation_quality")(
    _make_interaction_evaluator("conversation_quality", "Overall conversation quality assessment"),
)
ContextRetentionEvaluator = register_evaluator("context_retention")(
    _make_interaction_evaluator("context_retention", "Evaluates if earlier context is retained in later turns"),
)
