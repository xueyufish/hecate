"""Agent evaluators using LLM-as-Judge pattern (moved from agent_evaluators.py).

Provides five evaluators that use a configurable LLM to judge agent
response quality, registered via ``@register_evaluator``.
"""

from __future__ import annotations

import json
import logging
import os

import httpx

from hecate.services.evaluation.evaluator import Evaluator
from hecate.services.evaluation.prompts import (
    COMPLETENESS_PROMPT,
    CORRECTNESS_PROMPT,
    RELEVANCY_PROMPT,
    TASK_COMPLETION_PROMPT,
    TOOL_CALL_ACCURACY_PROMPT,
)
from hecate.services.evaluation.registry import register_evaluator
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
    """Call an LLM with a judge prompt and parse the JSON response."""
    config = llm_config or LLMConfig()
    api_base = config.api_base or "https://api.openai.com/v1"
    api_key = os.environ.get("OPENAI_API_KEY", "")

    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{api_base}/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": config.model,
                "temperature": config.temperature,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=60.0,
        )
        response.raise_for_status()

    content = response.json()["choices"][0]["message"]["content"]
    content = content.strip()
    if content.startswith("```"):
        lines = content.split("\n")
        content = "\n".join(lines[1:-1])

    return json.loads(content)


@register_evaluator("correctness")
class CorrectnessEvaluator(Evaluator):
    """Compare generated answer against expected answer using LLM-as-Judge."""

    category = "result"

    @property
    def name(self) -> str:
        return "correctness"

    @property
    def description(self) -> str:
        return "Compares generated answer against expected answer using LLM-as-Judge"

    async def evaluate(self, input: EvalInput) -> EvalOutput:
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


@register_evaluator("relevancy")
class RelevancyEvaluator(Evaluator):
    """Assess how relevant the generated answer is to the user query."""

    category = "result"

    @property
    def name(self) -> str:
        return "relevancy"

    @property
    def description(self) -> str:
        return "Assesses how relevant the generated answer is to the user query"

    async def evaluate(self, input: EvalInput) -> EvalOutput:
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


@register_evaluator("completeness")
class CompletenessEvaluator(Evaluator):
    """Measure whether the answer covers all aspects of the query."""

    category = "result"

    @property
    def name(self) -> str:
        return "completeness"

    @property
    def description(self) -> str:
        return "Measures whether the answer covers all aspects of the user query"

    async def evaluate(self, input: EvalInput) -> EvalOutput:
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


@register_evaluator("tool_call_accuracy")
class ToolCallAccuracyEvaluator(Evaluator):
    """Evaluate whether the agent used tools correctly and appropriately."""

    category = "process"

    @property
    def name(self) -> str:
        return "tool_call_accuracy"

    @property
    def description(self) -> str:
        return "Evaluates whether tool calls were appropriate and correct for the task"

    async def evaluate(self, input: EvalInput) -> EvalOutput:
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


@register_evaluator("task_completion")
class TaskCompletionEvaluator(Evaluator):
    """Evaluate whether the agent successfully completed the requested task."""

    category = "result"

    @property
    def name(self) -> str:
        return "task_completion"

    @property
    def description(self) -> str:
        return "Evaluates whether the agent successfully completed the requested task"

    async def evaluate(self, input: EvalInput) -> EvalOutput:
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
