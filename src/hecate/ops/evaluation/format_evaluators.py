"""Deterministic format evaluators.

Four evaluators that operate on the generated answer without invoking any
LLM call. Used both as fast regression checks and as ground-truth
assertions on synthesized items (the AI synthesis pipeline tags items with
``strategy:*`` so these evaluators can score them in CI runs without
incurring LLM cost).

- :class:`ContainsEvaluator` — checks ``expected_answer`` (or a pattern from
  ``metadata["pattern"]``) appears as a substring of the generated answer.
- :class:`ExactMatchEvaluator` — checks the generated answer equals the
  expected answer after ``.strip()``.
- :class:`IsJsonEvaluator` — checks the generated answer parses as JSON.
- :class:`RegexMatchEvaluator` — checks the generated answer matches a regex
  pattern from ``metadata["pattern"]``.

All four share the same shape: zero LLM calls, ``Score.source="deterministic"``,
single ``Score`` output with ``value`` in {0.0, 1.0}, and ``metric_name``
matching the canonical short name.
"""

from __future__ import annotations

import json
import re

from hecate.ops.evaluation.evaluator import BuiltinEvaluator
from hecate.ops.evaluation.types import EvalInput, EvalOutput, Score


class ContainsEvaluator(BuiltinEvaluator):
    """Substring containment check."""

    @property
    def name(self) -> str:
        return "contains"

    @property
    def description(self) -> str:
        return "Checks the expected answer appears as a substring of the generated answer."

    async def evaluate(self, input: EvalInput) -> EvalOutput:
        needle = _pattern_or_expected(input)
        if needle is None:
            return EvalOutput(
                scores=[
                    Score(
                        metric_name=self.name,
                        value=0.0,
                        reasoning="No expected_answer or metadata.pattern provided",
                        source="deterministic",
                    )
                ]
            )
        haystack = input.generated_answer or ""
        value = 1.0 if needle in haystack else 0.0
        reasoning = f"Found substring {needle!r}" if value == 1.0 else f"Substring {needle!r} not in generated answer"
        return EvalOutput(
            scores=[Score(metric_name=self.name, value=value, reasoning=reasoning, source="deterministic")]
        )


class ExactMatchEvaluator(BuiltinEvaluator):
    """Exact equality after strip."""

    @property
    def name(self) -> str:
        return "exact_match"

    @property
    def description(self) -> str:
        return "Checks the generated answer equals the expected answer after strip."

    async def evaluate(self, input: EvalInput) -> EvalOutput:
        if input.expected_answer is None:
            return EvalOutput(
                scores=[
                    Score(
                        metric_name=self.name,
                        value=0.0,
                        reasoning="No expected_answer provided",
                        source="deterministic",
                    )
                ]
            )
        expected = input.expected_answer.strip()
        actual = (input.generated_answer or "").strip()
        value = 1.0 if actual == expected else 0.0
        reasoning = "Exact match" if value == 1.0 else f"Expected {expected!r}, got {actual!r}"
        return EvalOutput(
            scores=[Score(metric_name=self.name, value=value, reasoning=reasoning, source="deterministic")]
        )


class IsJsonEvaluator(BuiltinEvaluator):
    """Generated answer parses as JSON."""

    @property
    def name(self) -> str:
        return "is_json"

    @property
    def description(self) -> str:
        return "Checks the generated answer parses as JSON."

    async def evaluate(self, input: EvalInput) -> EvalOutput:
        text = input.generated_answer or ""
        try:
            json.loads(text)
            value = 1.0
            reasoning = "Parses as JSON"
        except (ValueError, TypeError) as exc:
            value = 0.0
            reasoning = f"JSON parse error: {exc}"
        return EvalOutput(
            scores=[Score(metric_name=self.name, value=value, reasoning=reasoning, source="deterministic")]
        )


class RegexMatchEvaluator(BuiltinEvaluator):
    """Generated answer matches a regex pattern."""

    @property
    def name(self) -> str:
        return "regex_match"

    @property
    def description(self) -> str:
        return "Checks the generated answer matches a regex pattern from metadata.pattern."

    async def evaluate(self, input: EvalInput) -> EvalOutput:
        pattern = input.metadata.get("pattern") if input.metadata else None
        if not pattern:
            return EvalOutput(
                scores=[
                    Score(
                        metric_name=self.name,
                        value=0.0,
                        reasoning="metadata.pattern is required",
                        source="deterministic",
                    )
                ]
            )
        try:
            regex = re.compile(pattern)
        except re.error as exc:
            return EvalOutput(
                scores=[
                    Score(
                        metric_name=self.name,
                        value=0.0,
                        reasoning=f"Invalid regex: {exc}",
                        source="deterministic",
                    )
                ]
            )
        haystack = input.generated_answer or ""
        value = 1.0 if regex.search(haystack) else 0.0
        reasoning = f"Pattern {pattern!r} matched" if value == 1.0 else f"Pattern {pattern!r} did not match"
        return EvalOutput(
            scores=[Score(metric_name=self.name, value=value, reasoning=reasoning, source="deterministic")]
        )


def _pattern_or_expected(input: EvalInput) -> str | None:
    """Resolve substring needle: explicit ``metadata.pattern`` wins, else
    fall back to ``expected_answer``."""
    if input.metadata and "pattern" in input.metadata:
        return str(input.metadata["pattern"])
    return input.expected_answer
