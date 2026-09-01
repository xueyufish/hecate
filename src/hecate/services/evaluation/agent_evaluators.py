"""Agent evaluators using LLM-as-Judge pattern.

Provides five evaluators that use a configurable LLM to judge agent
response quality:

- :class:`CorrectnessEvaluator` — compares answer against ground truth
- :class:`RelevancyEvaluator` — assesses answer relevance to query
- :class:`CompletenessEvaluator` — measures query coverage completeness
- :class:`ToolCallAccuracyEvaluator` — evaluates tool call correctness
- :class:`TaskCompletionEvaluator` — evaluates task completion quality
"""

from __future__ import annotations

import json
import logging

from hecate.services.evaluation.evaluator import Evaluator
from hecate.services.evaluation.prompts import (
    COMPLETENESS_PROMPT,
    CORRECTNESS_PROMPT,
    RELEVANCY_PROMPT,
    TASK_COMPLETION_PROMPT,
    TOOL_CALL_ACCURACY_PROMPT,
)
from hecate.services.evaluation.types import (
    EvalInput,
    EvalOutput,
    LLMConfig,
    Score,
    Timer,
)

logger = logging.getLogger(__name__)


async def _call_llm_judge(
    prompt: str,
    llm_config: LLMConfig | None = None,
) -> dict:
    """Call an LLM with a judge prompt via the LLM gateway and parse the JSON response.

    Args:
        prompt: The formatted judge prompt.
        llm_config: Optional per-call LLM configuration.

    Returns:
        Dict with "score" (float) and "reasoning" (str).

    Raises:
        RuntimeError: If the LLM call fails or response is invalid.
    """
    from hecate_llm.service import llm_service

    config = llm_config or LLMConfig()
    try:
        response = await llm_service.chat(
            messages=[{"role": "user", "content": prompt}],
            model=config.model,
            temperature=config.temperature,
            timeout=60.0,
        )
    except Exception as exc:
        raise RuntimeError(f"LLM-as-judge call failed: {exc}") from exc

    content = (response.content or "").strip()
    if content.startswith("```"):
        lines = content.split("\n")
        content = "\n".join(lines[1:-1])

    return json.loads(content)


class CorrectnessEvaluator(Evaluator):
    """Compare generated answer against expected answer using LLM-as-Judge."""

    @property
    def name(self) -> str:
        return "correctness"

    @property
    def description(self) -> str:
        return "Compares generated answer against expected answer using LLM-as-Judge"

    async def evaluate(self, input: EvalInput) -> EvalOutput:
        """Evaluate correctness of the generated answer.

        Returns Score(value=-1.0) when no expected_answer is provided.
        """
        with Timer() as timer:
            if input.expected_answer is None:
                score = Score(
                    metric_name=self.name,
                    value=-1.0,
                    reasoning="No expected_answer provided",
                    source="llm_judge",
                )
                return EvalOutput(scores=[score], duration_ms=timer.elapsed_ms)

            prompt = CORRECTNESS_PROMPT.format(
                query=input.query,
                expected_answer=input.expected_answer,
                answer=input.generated_answer,
            )
            try:
                result = await _call_llm_judge(prompt, self.llm_config)
                score = Score(
                    metric_name=self.name,
                    value=float(result["score"]),
                    reasoning=result.get("reasoning"),
                    source="llm_judge",
                )
            except Exception as e:
                logger.error("Correctness evaluation failed: %s", e)
                score = Score(
                    metric_name=self.name,
                    value=-1.0,
                    reasoning=f"LLM judge error: {e}",
                    source="llm_judge",
                )

        return EvalOutput(scores=[score], duration_ms=timer.elapsed_ms)


class RelevancyEvaluator(Evaluator):
    """Assess how relevant the generated answer is to the user query."""

    @property
    def name(self) -> str:
        return "relevancy"

    @property
    def description(self) -> str:
        return "Assesses how relevant the generated answer is to the user query"

    async def evaluate(self, input: EvalInput) -> EvalOutput:
        """Evaluate relevance of the answer to the query."""
        with Timer() as timer:
            prompt = RELEVANCY_PROMPT.format(
                query=input.query,
                answer=input.generated_answer,
            )
            try:
                result = await _call_llm_judge(prompt, self.llm_config)
                score = Score(
                    metric_name=self.name,
                    value=float(result["score"]),
                    reasoning=result.get("reasoning"),
                    source="llm_judge",
                )
            except Exception as e:
                logger.error("Relevancy evaluation failed: %s", e)
                score = Score(
                    metric_name=self.name,
                    value=-1.0,
                    reasoning=f"LLM judge error: {e}",
                    source="llm_judge",
                )

        return EvalOutput(scores=[score], duration_ms=timer.elapsed_ms)


class CompletenessEvaluator(Evaluator):
    """Measure whether the answer covers all aspects of the query."""

    @property
    def name(self) -> str:
        return "completeness"

    @property
    def description(self) -> str:
        return "Measures whether the answer covers all aspects of the user query"

    async def evaluate(self, input: EvalInput) -> EvalOutput:
        """Evaluate completeness of the answer relative to the query."""
        with Timer() as timer:
            prompt = COMPLETENESS_PROMPT.format(
                query=input.query,
                answer=input.generated_answer,
            )
            try:
                result = await _call_llm_judge(prompt, self.llm_config)
                score = Score(
                    metric_name=self.name,
                    value=float(result["score"]),
                    reasoning=result.get("reasoning"),
                    source="llm_judge",
                )
            except Exception as e:
                logger.error("Completeness evaluation failed: %s", e)
                score = Score(
                    metric_name=self.name,
                    value=-1.0,
                    reasoning=f"LLM judge error: {e}",
                    source="llm_judge",
                )

        return EvalOutput(scores=[score], duration_ms=timer.elapsed_ms)


class ToolCallAccuracyEvaluator(Evaluator):
    """Evaluate whether the agent used tools correctly and appropriately."""

    @property
    def name(self) -> str:
        return "tool_call_accuracy"

    @property
    def description(self) -> str:
        return "Evaluates whether tool calls were appropriate and correct for the task"

    async def evaluate(self, input: EvalInput) -> EvalOutput:
        """Evaluate tool call accuracy using LLM-as-Judge.

        Returns Score(value=-1.0) when no tool_calls are provided.
        """
        with Timer() as timer:
            if not input.tool_calls:
                score = Score(
                    metric_name=self.name,
                    value=-1.0,
                    reasoning="No tool_calls provided",
                    source="llm_judge",
                )
                return EvalOutput(scores=[score], duration_ms=timer.elapsed_ms)

            tool_calls_str = json.dumps(input.tool_calls, indent=2)
            prompt = TOOL_CALL_ACCURACY_PROMPT.format(
                query=input.query,
                tool_calls=tool_calls_str,
                answer=input.generated_answer,
            )
            try:
                result = await _call_llm_judge(prompt, self.llm_config)
                score = Score(
                    metric_name=self.name,
                    value=float(result["score"]),
                    reasoning=result.get("reasoning"),
                    source="llm_judge",
                )
            except Exception as e:
                logger.error("Tool call accuracy evaluation failed: %s", e)
                score = Score(
                    metric_name=self.name,
                    value=-1.0,
                    reasoning=f"LLM judge error: {e}",
                    source="llm_judge",
                )

        return EvalOutput(scores=[score], duration_ms=timer.elapsed_ms)


class TaskCompletionEvaluator(Evaluator):
    """Evaluate whether the agent successfully completed the requested task."""

    @property
    def name(self) -> str:
        return "task_completion"

    @property
    def description(self) -> str:
        return "Evaluates whether the agent successfully completed the requested task"

    async def evaluate(self, input: EvalInput) -> EvalOutput:
        """Evaluate task completion using LLM-as-Judge."""
        with Timer() as timer:
            prompt = TASK_COMPLETION_PROMPT.format(
                query=input.query,
                answer=input.generated_answer,
            )
            try:
                result = await _call_llm_judge(prompt, self.llm_config)
                score = Score(
                    metric_name=self.name,
                    value=float(result["score"]),
                    reasoning=result.get("reasoning"),
                    source="llm_judge",
                )
            except Exception as e:
                logger.error("Task completion evaluation failed: %s", e)
                score = Score(
                    metric_name=self.name,
                    value=-1.0,
                    reasoning=f"LLM judge error: {e}",
                    source="llm_judge",
                )

        return EvalOutput(scores=[score], duration_ms=timer.elapsed_ms)
