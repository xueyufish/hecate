"""Unit tests for DegradationEngine."""

from __future__ import annotations

from hecate.services.context.degradation import DegradationEngine
from hecate.services.context.token_counter import TokenCounter


class TestDegradationEngine:
    """Tests for the DegradationEngine class."""

    def test_drop_low_priority_removes_low(self) -> None:
        """Test that drop_low_priority removes low priority messages."""
        engine = DegradationEngine(TokenCounter("gpt-4o"))

        messages = [
            {"role": "user", "content": "Old question"},
            {"role": "assistant", "content": "Old answer"},
            {"role": "user", "content": "Recent question"},
            {"role": "assistant", "content": "Recent answer"},
        ]
        priorities = ["low", "low", "critical", "high"]
        target_tokens = 100  # Large enough to keep critical/high

        result = engine.drop_low_priority(messages, priorities, target_tokens)

        # Should keep critical and high priority messages
        contents = [m.get("content", "") for m in result]
        assert "Recent question" in contents
        assert "Recent answer" in contents

    def test_drop_low_priority_preserves_critical(self) -> None:
        """Test that critical messages are never dropped."""
        engine = DegradationEngine(TokenCounter("gpt-4o"))

        messages = [
            {"role": "system", "content": "System prompt"},
            {"role": "user", "content": "Old question"},
            {"role": "user", "content": "Current question"},
        ]
        priorities = ["critical", "low", "critical"]

        result = engine.drop_low_priority(messages, priorities, target_tokens=50)

        # System and current user message should be preserved
        contents = [m.get("content", "") for m in result]
        assert "System prompt" in contents
        assert "Current question" in contents

    def test_drop_low_priority_within_budget(self) -> None:
        """Test that no messages are dropped when within budget."""
        engine = DegradationEngine(TokenCounter("gpt-4o"))

        messages = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi"},
        ]
        priorities = ["critical", "high"]

        result = engine.drop_low_priority(messages, priorities, target_tokens=1000)

        # All messages should be kept
        assert len(result) == 2

    def test_compress_medium_priority(self) -> None:
        """Test that medium priority messages are compressed."""
        engine = DegradationEngine(TokenCounter("gpt-4o"))

        messages = [
            {"role": "system", "content": "System prompt"},
            {"role": "user", "content": "Old question about Python"},
            {"role": "assistant", "content": "Old answer about Python"},
            {"role": "user", "content": "Current question"},
        ]
        priorities = ["critical", "medium", "medium", "critical"]

        result = engine.compress_medium_priority(messages, priorities, target_tokens=50)

        # Should have system prompt, current question, and a summary
        assert len(result) >= 2

    def test_compress_medium_priority_no_medium(self) -> None:
        """Test compression when no medium priority messages exist."""
        engine = DegradationEngine(TokenCounter("gpt-4o"))

        messages = [
            {"role": "system", "content": "System prompt"},
            {"role": "user", "content": "Question"},
        ]
        priorities = ["critical", "critical"]

        result = engine.compress_medium_priority(messages, priorities, target_tokens=1000)

        # Should return messages unchanged
        assert len(result) == 2

    def test_emergency_summary(self) -> None:
        """Test emergency summary replaces history."""
        engine = DegradationEngine(TokenCounter("gpt-4o"))

        messages = [
            {"role": "system", "content": "System prompt"},
            {"role": "user", "content": "Help me build an API"},
            {"role": "assistant", "content": "Sure, let's start with FastAPI"},
            {"role": "user", "content": "Add authentication"},
            {"role": "assistant", "content": "Adding JWT auth"},
        ]

        result = engine.emergency_summary(messages, target_tokens=100)

        # Should have very few messages
        assert len(result) <= 2

        # Should have a system message with summary
        system_msgs = [m for m in result if m.get("role") == "system"]
        assert len(system_msgs) >= 1
        assert "Emergency context compression" in system_msgs[0]["content"]

    def test_emergency_summary_preserves_recent_user(self) -> None:
        """Test that emergency summary keeps the most recent user message."""
        engine = DegradationEngine(TokenCounter("gpt-4o"))

        messages = [
            {"role": "user", "content": "Old question"},
            {"role": "assistant", "content": "Old answer"},
            {"role": "user", "content": "Current question"},
        ]

        result = engine.emergency_summary(messages, target_tokens=200)

        # Should have the current user message
        user_msgs = [m for m in result if m.get("role") == "user"]
        assert any("Current question" in m.get("content", "") for m in user_msgs)

    def test_create_compression_summary(self) -> None:
        """Test compression summary creation."""
        engine = DegradationEngine(TokenCounter("gpt-4o"))

        messages = [
            {"role": "user", "content": "Tell me about Python"},
            {"role": "assistant", "content": "Python is great"},
            {"role": "user", "content": "What about Java?"},
        ]

        summary = engine._create_compression_summary(messages)
        assert isinstance(summary, str)
        assert len(summary) > 0

    def test_create_compression_summary_empty(self) -> None:
        """Test compression summary with empty messages."""
        engine = DegradationEngine(TokenCounter("gpt-4o"))

        summary = engine._create_compression_summary([])
        assert summary == "Previous conversation context."

    def test_extract_objective(self) -> None:
        """Test objective extraction."""
        engine = DegradationEngine(TokenCounter("gpt-4o"))

        messages = [
            {"role": "system", "content": "System"},
            {"role": "user", "content": "Build a REST API"},
        ]

        objective = engine._extract_objective(messages)
        assert "Build a REST API" in objective

    def test_extract_key_decisions(self) -> None:
        """Test key decisions extraction."""
        engine = DegradationEngine(TokenCounter("gpt-4o"))

        messages = [
            {"role": "assistant", "content": "I decided to use FastAPI for this project"},
            {"role": "assistant", "content": "We chose PostgreSQL as the database"},
        ]

        decisions = engine._extract_key_decisions(messages)
        assert "FastAPI" in decisions or "PostgreSQL" in decisions

    def test_extract_current_state(self) -> None:
        """Test current state extraction."""
        engine = DegradationEngine(TokenCounter("gpt-4o"))

        messages = [
            {"role": "user", "content": "Question"},
            {"role": "assistant", "content": "Current implementation status"},
        ]

        state = engine._extract_current_state(messages)
        assert "Current implementation status" in state

    def test_extract_current_state_no_assistant(self) -> None:
        """Test current state when no assistant message exists."""
        engine = DegradationEngine(TokenCounter("gpt-4o"))

        messages = [
            {"role": "user", "content": "Question"},
        ]

        state = engine._extract_current_state(messages)
        assert state == ""

    def test_drop_low_priority_mismatched_lengths(self) -> None:
        """Test drop_low_priority with mismatched message/priority lengths."""
        engine = DegradationEngine(TokenCounter("gpt-4o"))

        messages = [{"role": "user", "content": "Hello"}]
        priorities = ["critical", "high"]  # Mismatch

        result = engine.drop_low_priority(messages, priorities, target_tokens=1000)
        # Should return original messages
        assert result == messages

    def test_compress_medium_priority_mismatched_lengths(self) -> None:
        """Test compress_medium_priority with mismatched lengths."""
        engine = DegradationEngine(TokenCounter("gpt-4o"))

        messages = [{"role": "user", "content": "Hello"}]
        priorities = ["critical", "high"]  # Mismatch

        result = engine.compress_medium_priority(messages, priorities, target_tokens=1000)
        assert result == messages
