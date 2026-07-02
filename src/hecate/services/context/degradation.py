"""Degradation strategies for context budget management.

Implements three-level degradation to reduce context size when token budget
is exceeded:

- Level 1 (DROP): Remove low-priority messages
- Level 2 (COMPRESS): Compress medium-priority messages into summary
- Level 3 (EMERGENCY): Replace entire history with emergency summary
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from hecate.services.context.token_counter import TokenCounter

logger = logging.getLogger(__name__)


@dataclass
class MessageWithPriority:
    """A message with its assigned priority level."""

    message: dict[str, Any]
    priority: str  # "critical", "high", "medium", "low"
    index: int


class DegradationEngine:
    """Applies degradation strategies to reduce context size.

    The engine applies degradation levels sequentially until the context
    fits within the target token budget.
    """

    def __init__(self, token_counter: TokenCounter) -> None:
        """Initialize the degradation engine.

        Args:
            token_counter: TokenCounter instance for counting tokens.
        """
        self.token_counter = token_counter

    def drop_low_priority(
        self,
        messages: list[dict[str, Any]],
        priorities: list[str],
        target_tokens: int,
    ) -> list[dict[str, Any]]:
        """Level 1: Drop low-priority messages to fit within budget.

        Removes messages marked as "low" priority (early messages,
        system notifications) starting from the oldest.

        Args:
            messages: List of messages with priorities.
            priorities: Priority level for each message.
            target_tokens: Target token count to achieve.

        Returns:
            Filtered list of messages with low-priority messages removed.
        """
        if len(messages) != len(priorities):
            logger.warning("Messages and priorities length mismatch, returning original")
            return messages

        # Separate critical/high messages (keep) and medium/low messages (candidates for removal)
        keep_indices: list[int] = []
        drop_candidates: list[tuple[int, str]] = []  # (index, priority)

        for i, (_msg, priority) in enumerate(zip(messages, priorities, strict=False)):
            if priority in ("critical", "high"):
                keep_indices.append(i)
            else:
                drop_candidates.append((i, priority))

        # Calculate tokens from kept messages
        kept_messages = [messages[i] for i in keep_indices]
        current_tokens = self.token_counter.count_messages(kept_messages)

        if current_tokens <= target_tokens:
            # Already within budget, return only kept messages
            return kept_messages

        # Need to drop more - start with low priority, then medium
        # Sort candidates: low priority first (oldest first), then medium
        low_candidates = [(i, p) for i, p in drop_candidates if p == "low"]
        medium_candidates = [(i, p) for i, p in drop_candidates if p == "medium"]

        # Keep all medium candidates initially
        remaining_indices = keep_indices + [i for i, _ in medium_candidates]

        # Try adding low priority messages back if we have budget
        for idx, _ in low_candidates:
            test_indices = sorted(remaining_indices + [idx])
            test_messages = [messages[i] for i in test_indices]
            test_tokens = self.token_counter.count_messages(test_messages)
            if test_tokens <= target_tokens:
                remaining_indices.append(idx)

        # If still over budget, start removing medium priority messages (oldest first)
        remaining_indices.sort()
        for i in range(len(medium_candidates)):
            test_indices = remaining_indices[: len(remaining_indices) - i]
            test_messages = [messages[j] for j in test_indices]
            test_tokens = self.token_counter.count_messages(test_messages)
            if test_tokens <= target_tokens:
                return test_messages

        # Last resort: return only the kept messages
        return [messages[i] for i in sorted(keep_indices)]

    def compress_medium_priority(
        self,
        messages: list[dict[str, Any]],
        priorities: list[str],
        target_tokens: int,
    ) -> list[dict[str, Any]]:
        """Level 2: Compress medium-priority messages into a summary.

        Replaces medium-priority messages with a condensed summary,
        preserving critical and high-priority messages intact.

        Args:
            messages: List of messages.
            priorities: Priority level for each message.
            target_tokens: Target token count to achieve.

        Returns:
            List of messages with medium-priority messages compressed.
        """
        if len(messages) != len(priorities):
            logger.warning("Messages and priorities length mismatch, returning original")
            return messages

        # Separate messages by priority
        critical_high: list[dict[str, Any]] = []
        medium_messages: list[dict[str, Any]] = []

        for msg, priority in zip(messages, priorities, strict=False):
            if priority in ("critical", "high"):
                critical_high.append(msg)
            elif priority == "medium":
                medium_messages.append(msg)
            # Skip low priority (should have been dropped already)

        # If no medium messages to compress, return as-is
        if not medium_messages:
            return critical_high

        # Create a summary of medium-priority messages
        summary_content = self._create_compression_summary(medium_messages)
        summary_message = {
            "role": "system",
            "content": f"[Compressed history summary]: {summary_content}",
        }

        # Combine critical/high with compressed summary
        result = critical_high + [summary_message]
        result_tokens = self.token_counter.count_messages(result)

        # If still over budget, remove the summary too
        if result_tokens > target_tokens:
            return critical_high

        return result

    def emergency_summary(
        self,
        messages: list[dict[str, Any]],
        target_tokens: int,
    ) -> list[dict[str, Any]]:
        """Level 3: Replace all history with emergency summary.

        Creates a minimal context with just the essential information:
        original objective, key decisions, and current state.

        Args:
            messages: Full message history.
            target_tokens: Target token count to achieve.

        Returns:
            Minimal message list with emergency summary.
        """
        # Extract key information from message history
        objective = self._extract_objective(messages)
        key_decisions = self._extract_key_decisions(messages)
        current_state = self._extract_current_state(messages)

        # Build emergency summary
        summary_parts = []
        if objective:
            summary_parts.append(f"Objective: {objective}")
        if key_decisions:
            summary_parts.append(f"Key decisions: {key_decisions}")
        if current_state:
            summary_parts.append(f"Current state: {current_state}")

        summary_content = ". ".join(summary_parts) if summary_parts else "Context compressed due to token budget."

        result = [
            {
                "role": "system",
                "content": f"[Emergency context compression]: {summary_content}",
            }
        ]

        # Add the most recent user message if available
        for msg in reversed(messages):
            if msg.get("role") == "user":
                result.append(msg)
                break

        # Verify we're within budget
        result_tokens = self.token_counter.count_messages(result)
        if result_tokens > target_tokens:
            # Extreme case: just the summary
            return result[:1]

        return result

    def _create_compression_summary(self, messages: list[dict[str, Any]]) -> str:
        """Create a concise summary of messages for compression.

        Args:
            messages: Messages to summarize.

        Returns:
            Summary string.
        """
        # Extract key content from messages
        contents = []
        for msg in messages:
            content = msg.get("content", "")
            if isinstance(content, str) and content:
                # Truncate individual messages
                contents.append(content[:200])

        if not contents:
            return "Previous conversation context."

        # Create summary
        summary = "; ".join(contents[:5])  # Max 5 messages in summary
        if len(contents) > 5:
            summary += f" ... and {len(contents) - 5} more messages"

        return summary

    def _extract_objective(self, messages: list[dict[str, Any]]) -> str:
        """Extract the original objective from message history.

        Args:
            messages: Full message history.

        Returns:
            Objective string or empty string.
        """
        # Look for the first user message as the objective
        for msg in messages:
            if msg.get("role") == "user":
                content = msg.get("content", "")
                if isinstance(content, str):
                    return content[:500]
        return ""

    def _extract_key_decisions(self, messages: list[dict[str, Any]]) -> str:
        """Extract key decisions from assistant messages.

        Args:
            messages: Full message history.

        Returns:
            Summary of key decisions.
        """
        decisions = []
        for msg in messages:
            if msg.get("role") == "assistant":
                content = msg.get("content", "")
                if isinstance(content, str) and any(
                    keyword in content.lower() for keyword in ["decision", "chose", "selected", "decided", "will"]
                ):
                    decisions.append(content[:100])

        if not decisions:
            return ""

        return "; ".join(decisions[:3])

    def _extract_current_state(self, messages: list[dict[str, Any]]) -> str:
        """Extract current state from recent messages.

        Args:
            messages: Full message history.

        Returns:
            Current state description.
        """
        # Get the last assistant message
        for msg in reversed(messages):
            if msg.get("role") == "assistant":
                content = msg.get("content", "")
                if isinstance(content, str):
                    return content[:300]
        return ""
