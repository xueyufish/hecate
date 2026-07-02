"""Task work panel construction for structured context.

For conversations with many turns, constructs a structured work panel
that replaces raw message history with focused context:
- Current objective (from first user message)
- Recent exchanges (last 3 turns)
- Latest tool result
- Summary of older messages
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# Thresholds for work panel construction
_MIN_TURNS_FOR_PANEL = 3  # Don't create panel for short conversations
_RECENT_EXCHANGES = 3  # Number of recent exchanges to keep in full


class WorkPanelBuilder:
    """Constructs structured work panels from conversation history.

    When conversations get long, raw message history becomes noisy.
    Work panels organize context into structured sections that help
    the LLM focus on what matters.
    """

    def __init__(
        self,
        min_turns: int = _MIN_TURNS_FOR_PANEL,
        recent_exchanges: int = _RECENT_EXCHANGES,
    ) -> None:
        """Initialize the work panel builder.

        Args:
            min_turns: Minimum turns before creating a work panel.
            recent_exchanges: Number of recent exchanges to preserve.
        """
        self.min_turns = min_turns
        self.recent_exchanges = recent_exchanges

    def build_panel(
        self,
        messages: list[dict[str, Any]],
        priorities: list[str],
    ) -> list[dict[str, Any]]:
        """Build a structured work panel from message history.

        Args:
            messages: Full message history.
            priorities: Priority for each message.

        Returns:
            Structured message list optimized for context efficiency.
        """
        if not messages:
            return []

        # Count user turns
        user_turns = sum(1 for m in messages if m.get("role") == "user")

        # Don't create panel for short conversations
        if user_turns <= self.min_turns:
            return messages

        # Extract components
        objective = self._extract_objective(messages)
        recent_start = self._find_recent_boundary(messages)
        recent_messages = messages[recent_start:]
        older_messages = messages[:recent_start]

        # Build the panel
        panel: list[dict[str, Any]] = []

        # 1. Add system messages (preserve original)
        for _i, msg in enumerate(messages):
            if msg.get("role") == "system":
                panel.append(msg)

        # 2. Add objective summary if we have older messages
        if older_messages and objective:
            panel.append(
                {
                    "role": "system",
                    "content": f"[Original objective]: {objective}",
                }
            )

        # 3. Add summary of older messages
        if older_messages:
            summary = self._summarize_older_messages(older_messages)
            if summary:
                panel.append(
                    {
                        "role": "system",
                        "content": f"[Previous context summary]: {summary}",
                    }
                )

        # 4. Add recent messages in full
        for msg in recent_messages:
            panel.append(msg)

        logger.debug(
            f"Built work panel: {len(messages)} messages → {len(panel)} messages "
            f"({user_turns} user turns, kept {len(recent_messages)} recent)"
        )
        return panel

    def _extract_objective(self, messages: list[dict[str, Any]]) -> str:
        """Extract the original objective from the first user message.

        Args:
            messages: Full message history.

        Returns:
            Objective string or empty string.
        """
        for msg in messages:
            if msg.get("role") == "user":
                content = msg.get("content", "")
                if isinstance(content, str):
                    # Truncate to reasonable length
                    return content[:500]
        return ""

    def _find_recent_boundary(self, messages: list[dict[str, Any]]) -> int:
        """Find the boundary index for recent messages.

        Args:
            messages: Full message history.

        Returns:
            Index where recent messages start.
        """
        # Count backwards from end to find N recent exchanges
        exchanges_found = 0
        for i in range(len(messages) - 1, -1, -1):
            if messages[i].get("role") == "user":
                exchanges_found += 1
                if exchanges_found >= self.recent_exchanges:
                    return i

        # If not enough exchanges found, return start
        return 0

    def _summarize_older_messages(
        self,
        messages: list[dict[str, Any]],
    ) -> str:
        """Create a concise summary of older messages.

        Args:
            messages: Messages to summarize.

        Returns:
            Summary string.
        """
        # Extract key points from user and assistant messages
        key_points = []
        for msg in messages:
            role = msg.get("role", "")
            content = msg.get("content", "")

            if role == "user" and isinstance(content, str):
                # Truncate user messages
                key_points.append(f"User asked: {content[:100]}")
            elif role == "assistant" and isinstance(content, str):
                # Extract first sentence of assistant responses
                first_sentence = content.split(".")[0] if content else ""
                if first_sentence:
                    key_points.append(f"Assistant: {first_sentence[:100]}")
            elif role == "tool":
                # Note tool usage
                tool_call_id = msg.get("tool_call_id", "")
                key_points.append(f"Tool result for {tool_call_id[:20]}")

        if not key_points:
            return ""

        # Combine into summary (limit to 5 points)
        summary = "; ".join(key_points[:5])
        if len(key_points) > 5:
            summary += f" ... and {len(key_points) - 5} more exchanges"

        return summary

    def should_build_panel(
        self,
        messages: list[dict[str, Any]],
    ) -> bool:
        """Check if a work panel should be built.

        Args:
            messages: Full message history.

        Returns:
            True if panel construction is recommended.
        """
        user_turns = sum(1 for m in messages if m.get("role") == "user")
        return user_turns > self.min_turns
