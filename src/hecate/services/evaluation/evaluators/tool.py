"""Tool and process LLM-as-Judge evaluators."""

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


def _make_process_evaluator(eval_name: str, eval_description: str, needs_tool_calls: bool = False) -> type[Evaluator]:
    """Factory for creating process/tool LLM-judge evaluators."""

    class _ProcessEvaluator(Evaluator):
        category = "process"

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
                    tool_calls=json.dumps(input.tool_calls or [], indent=2),
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

    _ProcessEvaluator.__name__ = eval_name.title().replace("_", "") + "Evaluator"
    return _ProcessEvaluator


ToolSelectionAccuracyEvaluator = register_evaluator("tool_selection_accuracy")(
    _make_process_evaluator("tool_selection_accuracy", "Assesses tool selection correctness"),
)
ToolTrajectoryScoringEvaluator = register_evaluator("tool_trajectory_scoring")(
    _make_process_evaluator("tool_trajectory_scoring", "Scores the sequence of tool calls"),
)
ToolParameterAccuracyEvaluator = register_evaluator("tool_parameter_accuracy")(
    _make_process_evaluator("tool_parameter_accuracy", "Evaluates correctness of tool call parameters"),
)
ToolOrderCorrectnessEvaluator = register_evaluator("tool_order_correctness")(
    _make_process_evaluator("tool_order_correctness", "Checks if tool call order is logical"),
)
ReasoningQualityEvaluator = register_evaluator("reasoning_quality")(
    _make_process_evaluator("reasoning_quality", "Assesses overall reasoning quality"),
)
StepValidityEvaluator = register_evaluator("step_validity")(
    _make_process_evaluator("step_validity", "Validates individual reasoning steps"),
)
