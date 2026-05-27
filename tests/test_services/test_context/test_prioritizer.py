"""Unit tests for MessagePrioritizer."""

from __future__ import annotations

from hecate.services.context.prioritizer import MessagePrioritizer
from hecate.services.context.types import MessagePriority


class TestMessagePrioritizer:
    """Tests for the MessagePrioritizer class."""

    def test_assign_priorities_empty(self) -> None:
        """Test priority assignment with empty messages."""
        prioritizer = MessagePrioritizer()
        priorities = prioritizer.assign_priorities([])
        assert priorities == []

    def test_assign_priorities_system_critical(self) -> None:
        """Test that system messages are always CRITICAL."""
        prioritizer = MessagePrioritizer()
        messages = [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "Hello"},
        ]

        priorities = prioritizer.assign_priorities(messages)
        assert priorities[0] == MessagePriority.CRITICAL.value

    def test_assign_priorities_last_user_critical(self) -> None:
        """Test that the last user message is CRITICAL."""
        prioritizer = MessagePrioritizer()
        messages = [
            {"role": "user", "content": "Old question"},
            {"role": "assistant", "content": "Old answer"},
            {"role": "user", "content": "Current question"},
        ]

        priorities = prioritizer.assign_priorities(messages)
        assert priorities[-1] == MessagePriority.CRITICAL.value

    def test_assign_priorities_recent_high(self) -> None:
        """Test that recent exchanges get HIGH priority."""
        prioritizer = MessagePrioritizer(recent_window=2)
        messages = [
            {"role": "user", "content": "Q1"},
            {"role": "assistant", "content": "A1"},
            {"role": "user", "content": "Q2"},
            {"role": "assistant", "content": "A2"},
            {"role": "user", "content": "Q3"},
        ]

        priorities = prioritizer.assign_priorities(messages)

        # Recent messages should be HIGH or CRITICAL
        recent_priorities = priorities[-4:]  # Last 2 exchanges
        for p in recent_priorities:
            assert p in [MessagePriority.HIGH.value, MessagePriority.CRITICAL.value]

    def test_assign_priorities_old_low(self) -> None:
        """Test that old messages get LOW priority."""
        prioritizer = MessagePrioritizer(recent_window=1)
        messages = [
            {"role": "user", "content": "Old Q1"},
            {"role": "assistant", "content": "Old A1"},
            {"role": "user", "content": "Old Q2"},
            {"role": "assistant", "content": "Old A2"},
            {"role": "user", "content": "Recent Q"},
        ]

        priorities = prioritizer.assign_priorities(messages)

        # First few messages should be LOW or MEDIUM
        assert priorities[0] in [MessagePriority.LOW.value, MessagePriority.MEDIUM.value]

    def test_assign_priorities_tool_results(self) -> None:
        """Test priority assignment for tool results."""
        prioritizer = MessagePrioritizer(tool_result_recent=2)
        messages = [
            {"role": "user", "content": "Run code"},
            {"role": "assistant", "content": "Running..."},
            {"role": "tool", "content": "Result"},
            {"role": "user", "content": "Current question"},
        ]

        priorities = prioritizer.assign_priorities(messages)

        # Tool result should have a priority
        assert priorities[2] in [p.value for p in MessagePriority]

    def test_find_last_user_message(self) -> None:
        """Test finding the last user message index."""
        prioritizer = MessagePrioritizer()
        messages = [
            {"role": "user", "content": "Q1"},
            {"role": "assistant", "content": "A1"},
            {"role": "user", "content": "Q2"},
        ]

        idx = prioritizer._find_last_user_message(messages)
        assert idx == 2

    def test_find_last_user_message_none(self) -> None:
        """Test finding last user message when none exist."""
        prioritizer = MessagePrioritizer()
        messages = [
            {"role": "system", "content": "System"},
            {"role": "assistant", "content": "Response"},
        ]

        idx = prioritizer._find_last_user_message(messages)
        assert idx == -1

    def test_get_priority_summary(self) -> None:
        """Test getting priority summary."""
        prioritizer = MessagePrioritizer()
        messages = [
            {"role": "system", "content": "System"},
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi"},
        ]
        priorities = ["critical", "critical", "high"]

        summary = prioritizer.get_priority_summary(messages, priorities)
        assert summary["critical"] == 2
        assert summary["high"] == 1
        assert summary["medium"] == 0
        assert summary["low"] == 0

    def test_determine_priority_recency(self) -> None:
        """Test that recency affects priority."""
        prioritizer = MessagePrioritizer()

        # Test with a message at different positions
        msg = {"role": "user", "content": "Test"}

        # Recent message (high recency)
        priority_recent = prioritizer._determine_priority(
            message=msg,
            index=9,
            total=10,
            last_user_idx=9,
        )

        # Old message (low recency)
        priority_old = prioritizer._determine_priority(
            message=msg,
            index=1,
            total=10,
            last_user_idx=9,
        )

        # Recent should have higher priority
        assert MessagePriority.CRITICAL.value == priority_recent or MessagePriority.HIGH.value == priority_recent
        assert MessagePriority.LOW.value == priority_old or MessagePriority.MEDIUM.value == priority_old
