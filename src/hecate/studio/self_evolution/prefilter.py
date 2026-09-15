"""Rule-based trajectory pre-filter for the attribution queue.

Cheap heuristic pass before any LLM attribution: only trajectories with a
notable failure pattern (tool errors, timeouts, loops, user corrections)
consume attribution budget. Pattern names feed the attribution prompt as
context hints.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.models.evidence import EvidenceModel

# Patterns shared with the harvest correction signal (kept in sync by design:
# the harvest signal admits, the pre-filter annotates).
_CORRECTION_PHRASES = (
    "that's wrong",
    "no, i meant",
    "not what i asked",
    "incorrect",
    "try again",
    "you misunderstood",
)

_TIMEOUT_MARKERS = ("timeout", "timed out")

# A "streak" of tool errors worth attribution (>= 2 error results).
_TOOL_ERROR_STREAK = 2

# Repeated assistant content (same prefix) across the last N messages = loop.
_LOOP_WINDOW = 4
_LOOP_PREFIX_CHARS = 80


@dataclass
class PrefilterResult:
    """Outcome of the rule-based pre-filter."""

    hit: bool = False
    patterns: list[str] = field(default_factory=list)


class TrajectoryPrefilter:
    """Evaluates cheap failure-pattern heuristics over a trajectory."""

    async def evaluate(
        self,
        db: AsyncSession,
        conversation_id: Any,
        messages: list[dict[str, Any]],
    ) -> PrefilterResult:
        """Evaluate message- and evidence-level patterns.

        Args:
            db: Database session (for evidence lookups).
            conversation_id: Conversation whose tool evidence is inspected.
            messages: Projected user/assistant messages of the conversation.

        Returns:
            PrefilterResult with hit=True and all matched pattern names when
            any notable pattern fired.
        """
        patterns = self._message_patterns(messages)
        patterns.extend(await self._evidence_patterns(db, conversation_id))
        return PrefilterResult(hit=bool(patterns), patterns=patterns)

    def _message_patterns(self, messages: list[dict[str, Any]]) -> list[str]:
        """Detect user corrections and repetition loops in messages."""
        patterns: list[str] = []

        for message in messages:
            if message.get("role") != "user":
                continue
            content = str(message.get("content", "")).lower()
            if any(phrase in content for phrase in _CORRECTION_PHRASES):
                patterns.append("user_correction")
                break

        assistant_tail = [
            str(m.get("content", ""))[:_LOOP_PREFIX_CHARS]
            for m in messages[-_LOOP_WINDOW:]
            if m.get("role") == "assistant"
        ]
        if len(assistant_tail) >= 2 and len(set(assistant_tail)) < len(assistant_tail):
            patterns.append("loop_repetition")

        return patterns

    async def _evidence_patterns(self, db: AsyncSession, conversation_id: Any) -> list[str]:
        """Detect tool-error streaks and timeouts in captured evidence."""
        result = await db.execute(
            select(EvidenceModel).where(
                EvidenceModel.conversation_id == conversation_id,
                EvidenceModel.is_error.is_(True),
                ~EvidenceModel.deleted,
            )
        )
        error_rows = list(result.scalars().all())

        patterns: list[str] = []
        if len(error_rows) >= _TOOL_ERROR_STREAK:
            patterns.append("tool_error_streak")
        elif error_rows:
            patterns.append("tool_error")
        if any(any(marker in (row.raw_content or "").lower() for marker in _TIMEOUT_MARKERS) for row in error_rows):
            patterns.append("timeout")
        return patterns
