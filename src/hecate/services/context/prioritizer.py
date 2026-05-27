"""Message priority assignment for context degradation decisions.

Assigns priority levels (critical/high/medium/low) to messages based on:
- Role (system/user/assistant/tool)
- Recency (recent messages get higher priority)
- Content type (tool results, errors get different treatment)
"""

from __future__ import annotations

import logging
from typing import Any

from hecate.services.context.types import MessagePriority

logger = logging.getLogger(__name__)

# Default thresholds for priority assignment
_RECENT_WINDOW = 3  # Last N exchanges get HIGH priority
_TOOL_RESULT_RECENT = 2  # Tool results within last N turns get HIGH


class MessagePrioritizer:
    """Assigns priority levels to messages for context management.

    Priorities determine which messages are kept, compressed, or dropped
    when the context exceeds token budget limits.
    """

    def __init__(
        self,
        recent_window: int = _RECENT_WINDOW,
        tool_result_recent: int = _TOOL_RESULT_RECENT,
    ) -> None:
        """Initialize the prioritizer.

        Args:
            recent_window: Number of recent exchanges to mark as HIGH.
            tool_result_recent: Number of recent turns for HIGH tool results.
        """
        self.recent_window = recent_window
        self.tool_result_recent = tool_result_recent

    def assign_priorities(
        self,
        messages: list[dict[str, Any]],
    ) -> list[str]:
        """Assign priority levels to each message.

        Args:
            messages: List of message dicts with 'role' and optionally 'content'.

        Returns:
            List of priority strings (critical/high/medium/low) aligned with messages.
        """
        if not messages:
            return []

        priorities: list[str] = []
        total = len(messages)

        # Find the last user message index (current user message gets CRITICAL)
        last_user_idx = self._find_last_user_message(messages)

        for i, msg in enumerate(messages):
            priority = self._determine_priority(
                message=msg,
                index=i,
                total=total,
                last_user_idx=last_user_idx,
            )
            priorities.append(priority.value)

        return priorities

    def _determine_priority(
        self,
        message: dict[str, Any],
        index: int,
        total: int,
        last_user_idx: int,
    ) -> MessagePriority:
        """Determine priority for a single message.

        Args:
            message: The message dict.
            index: Message index in the list.
            total: Total number of messages.
            last_user_idx: Index of the last user message.

        Returns:
            MessagePriority level for the message.
        """
        role = message.get("role", "user")

        # System messages are always CRITICAL
        if role == "system":
            return MessagePriority.CRITICAL

        # The last user message (current query) is CRITICAL
        if index == last_user_idx:
            return MessagePriority.CRITICAL

        # Calculate recency (0 = oldest, 1 = newest)
        recency = index / max(1, total - 1)

        # Recent messages (last N exchanges) get HIGH priority
        if recency >= (1 - (self.recent_window * 2) / max(1, total)):
            # Recent user and assistant messages
            if role in ("user", "assistant"):
                return MessagePriority.HIGH

            # Recent tool results
            if role == "tool":
                # Check if this is a recent tool result
                if recency >= (1 - (self.tool_result_recent * 2) / max(1, total)):
                    return MessagePriority.HIGH
                return MessagePriority.MEDIUM

        # Medium priority for middle-aged messages
        if recency >= 0.3:
            return MessagePriority.MEDIUM

        # Low priority for old messages
        return MessagePriority.LOW

    def _find_last_user_message(
        self,
        messages: list[dict[str, Any]],
    ) -> int:
        """Find the index of the last user message.

        Args:
            messages: List of messages.

        Returns:
            Index of the last user message, or -1 if none found.
        """
        for i in range(len(messages) - 1, -1, -1):
            if messages[i].get("role") == "user":
                return i
        return -1

    def get_priority_summary(
        self,
        messages: list[dict[str, Any]],
        priorities: list[str],
    ) -> dict[str, int]:
        """Get summary of message priorities for debugging.

        Args:
            messages: List of messages.
            priorities: Assigned priorities.

        Returns:
            Dict with count of messages at each priority level.
        """
        summary = {
            MessagePriority.CRITICAL.value: 0,
            MessagePriority.HIGH.value: 0,
            MessagePriority.MEDIUM.value: 0,
            MessagePriority.LOW.value: 0,
        }

        for priority in priorities:
            if priority in summary:
                summary[priority] += 1

        return summary
