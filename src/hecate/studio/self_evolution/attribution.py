"""LLM-driven failure attribution (AgentRx taxonomy) with skill drafting.

One attribution call per learning input: the model classifies the failure
into one of the ten AgentRx categories and, when the failure is learnable,
drafts a knowledge-only candidate skill (procedure + guardrails). Rule-based
heuristics stay in the pre-filter; all natural-language reasoning happens
here, mirroring the industry pattern (GEPA/ACE-style reflective analysis).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from hecate_llm.service import llm_service

logger = logging.getLogger(__name__)


class FailureCategory(StrEnum):
    """AgentRx failure taxonomy (10 types)."""

    INSTRUCTION_ADHERENCE = "instruction_adherence"
    INFORMATION_INVENTION = "information_invention"
    INVALID_INVOCATION = "invalid_invocation"
    TOOL_OUTPUT_MISINTERPRETATION = "tool_output_misinterpretation"
    INTENT_PLAN_MISALIGNMENT = "intent_plan_misalignment"
    UNDERSPECIFIED_INTENT = "underspecified_intent"
    UNSUPPORTED_INTENT = "unsupported_intent"
    GUARDRAILS_TRIGGERED = "guardrails_triggered"
    SYSTEM_FAILURE = "system_failure"
    INCONCLUSIVE = "inconclusive"


# Categories that must not produce candidates: platform-side faults, missing
# signal, and cases where existing guardrails already worked as designed.
NON_LEARNABLE_CATEGORIES = {
    FailureCategory.SYSTEM_FAILURE,
    FailureCategory.INCONCLUSIVE,
    FailureCategory.GUARDRAILS_TRIGGERED,
}

_MIN_CONFIDENCE = 0.5

_SYSTEM_PROMPT = (
    "You are a failure analyst for LLM agent trajectories. You classify "
    "failures using the AgentRx taxonomy and, when worthwhile, draft a "
    "reusable knowledge skill so the agent avoids the failure next time. "
    "Respond with a single JSON object and nothing else."
)

_USER_PROMPT_TEMPLATE = """Analyze the failed agent trajectory below.

Patterns detected by the rule pre-filter: {patterns}

Trajectory messages:
{transcript}

Tool evidence (captured results, may be empty):
{evidence}

Classify the root cause into exactly one of these categories:
instruction_adherence, information_invention, invalid_invocation,
tool_output_misinterpretation, intent_plan_misalignment,
underspecified_intent, unsupported_intent, guardrails_triggered,
system_failure, inconclusive.

Rules:
- Use system_failure for platform/model-side faults the agent cannot prevent.
- Use inconclusive when evidence is insufficient (confidence < 0.5).
- Use guardrails_triggered when an existing safety control worked as intended.
- Otherwise the failure is learnable: draft a knowledge skill so the agent
  handles the situation better next time.

Respond with JSON:
{{
  "category": "<category>",
  "confidence": <0.0-1.0>,
  "root_cause": "<one sentence>",
  "evidence": [{{"message_index": <int>, "quote": "<short excerpt>"}}],
  "skill": {{
    "name": "<kebab-case-slug>",
    "description": "<when should the agent apply this skill>",
    "procedure": "<step-by-step guidance, plain markdown prose>",
    "guardrails": "<what to avoid, derived from this failure class>"
  }}
}}

Omit the "skill" object when the category is system_failure, inconclusive or
guardrails_triggered, or when no durable skill is worthwhile. Never include
executable code in the skill draft."""


@dataclass
class AttributionResult:
    """Outcome of one attribution call."""

    category: FailureCategory = FailureCategory.INCONCLUSIVE
    confidence: float = 0.0
    root_cause: str = ""
    evidence_refs: list[dict] = field(default_factory=list)
    skill_draft: dict | None = None  # {name, description, procedure, guardrails}
    tokens_used: int = 0
    llm_calls: int = 0
    parse_error: str | None = None

    @property
    def learnable(self) -> bool:
        """True when the category merits a candidate and confidence suffices."""
        return (
            self.category not in NON_LEARNABLE_CATEGORIES
            and self.confidence >= _MIN_CONFIDENCE
            and self.skill_draft is not None
        )


class FailureAttributor:
    """Attributes failures via LLM and drafts knowledge-only skill candidates."""

    def __init__(self, model: str | None = None) -> None:
        if model is None:
            from hecate.core.config import settings

            model = settings.SKILL_EVOLUTION_ATTRIBUTION_MODEL
        self._model = model

    async def attribute(
        self,
        messages: list[dict[str, Any]],
        patterns: list[str],
        evidence: list[dict[str, Any]],
    ) -> AttributionResult:
        """Run one attribution call over a trajectory.

        Never raises for model errors: failures return an inconclusive
        result with the error recorded, so a run continues with remaining
        inputs.
        """
        prompt = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {
                "role": "user",
                "content": _USER_PROMPT_TEMPLATE.format(
                    patterns=", ".join(patterns) or "none",
                    transcript=_transcript(messages),
                    evidence=json.dumps(evidence[:20], ensure_ascii=False, default=str) if evidence else "none",
                ),
            },
        ]
        result = AttributionResult()
        try:
            response = await llm_service.chat(
                messages=prompt,
                model=self._model,
                temperature=0.0,
                max_tokens=1200,
                timeout=60.0,
            )
        except Exception as exc:
            logger.warning("Attribution LLM call failed: %s", exc)
            result.parse_error = f"llm_call_failed: {exc}"
            result.llm_calls = 1
            return result

        result.llm_calls = 1
        usage = getattr(response, "usage", None)
        result.tokens_used = int(getattr(usage, "total_tokens", 0) or 0)

        parsed = _parse_json_object(getattr(response, "content", "") or "")
        if parsed is None:
            result.parse_error = "unparseable_response"
            return result

        try:
            result.category = FailureCategory(str(parsed.get("category", "inconclusive")))
        except ValueError:
            result.category = FailureCategory.INCONCLUSIVE
        result.confidence = _clamp(float(parsed.get("confidence", 0.0) or 0.0))
        result.root_cause = str(parsed.get("root_cause", ""))[:500]
        evidence_refs = parsed.get("evidence") or []
        if isinstance(evidence_refs, list):
            result.evidence_refs = [ref for ref in evidence_refs if isinstance(ref, dict) and ref.get("quote")][:10]

        skill = parsed.get("skill")
        if isinstance(skill, dict) and skill.get("name"):
            result.skill_draft = {
                "name": str(skill.get("name", ""))[:255],
                "description": str(skill.get("description", ""))[:2000],
                "procedure": str(skill.get("procedure", "")),
                "guardrails": str(skill.get("guardrails", "")),
            }
        return result


def _transcript(messages: list[dict[str, Any]]) -> str:
    """Render a compact numbered transcript for the prompt."""
    lines: list[str] = []
    for i, message in enumerate(messages[:60]):
        role = message.get("role", "?")
        content = str(message.get("content", ""))[:600]
        lines.append(f"{i}. [{role}] {content}")
    return "\n".join(lines)


def _parse_json_object(text: str) -> dict | None:
    """Extract the first JSON object from model output."""
    text = text.strip()
    if text.startswith("{") and text.endswith("}"):
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        try:
            return json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return None
    return None


def _clamp(value: float) -> float:
    """Clamp confidence into [0, 1]."""
    return max(0.0, min(1.0, value))
