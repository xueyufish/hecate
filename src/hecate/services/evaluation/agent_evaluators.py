"""Agent evaluators using LLM-as-Judge pattern.

Provides three evaluators that use a configurable LLM to judge agent
response quality:

- :class:`CorrectnessEvaluator` — compares answer against ground truth
- :class:`RelevancyEvaluator` — assesses answer relevance to query
- :class:`CompletenessEvaluator` — measures query coverage completeness
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
    """Call an LLM with a judge prompt and parse the JSON response.

    Args:
        prompt: The formatted judge prompt.
        llm_config: Optional per-call LLM configuration.

    Returns:
        Dict with "score" (float) and "reasoning" (str).

    Raises:
        RuntimeError: If the LLM call fails or response is invalid.
    """
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

    # Strip markdown code fences if present
    content = content.strip()
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
