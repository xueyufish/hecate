"""Unit tests for CompressionPipeline."""

from __future__ import annotations

from hecate.services.memory.compression import CompressionPipeline


class TestCompressionPipeline:
    """Tests for the CompressionPipeline class."""

    def test_snip_short_conversation(self) -> None:
        """Test snip with short conversation returns unchanged."""
        pipeline = CompressionPipeline()
        messages = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi!"},
        ]

        result = pipeline.snip(messages, recent_window=6)

        assert result.level_applied == "none"
        assert result.compressed_count == 2

    def test_snip_removes_tool_results(self) -> None:
        """Test that snip removes old tool results."""
        pipeline = CompressionPipeline()
        messages = [
            {"role": "user", "content": "Q1"},
            {"role": "assistant", "content": "A1"},
            {"role": "tool", "content": "tool result"},
            {"role": "user", "content": "Q2"},
            {"role": "assistant", "content": "A2"},
        ]

        result = pipeline.snip(messages, recent_window=2)

        # Should have removed old tool result
        roles = [m["role"] for m in result.messages]
        assert "tool" not in roles

    def test_snip_preserves_system_messages(self) -> None:
        """Test that snip preserves system messages."""
        pipeline = CompressionPipeline()
        messages = [
            {"role": "system", "content": "System prompt"},
            {"role": "user", "content": "Q1"},
            {"role": "assistant", "content": "A1"},
            {"role": "user", "content": "Q2"},
            {"role": "assistant", "content": "A2"},
        ]

        result = pipeline.snip(messages, recent_window=2)

        system_msgs = [m for m in result.messages if m["role"] == "system"]
        assert len(system_msgs) == 1

    def test_microcompact_merges_consecutive(self) -> None:
        """Test that microcompact merges consecutive same-role messages."""
        pipeline = CompressionPipeline()
        messages = [
            {"role": "user", "content": "Hello"},
            {"role": "user", "content": "How are you?"},
            {"role": "assistant", "content": "Hi!"},
            {"role": "assistant", "content": "I'm good."},
        ]

        result = pipeline.microcompact(messages)

        assert result.compressed_count == 2
        assert "Hello" in result.messages[0]["content"]
        assert "How are you?" in result.messages[0]["content"]

    def test_microcompact_empty(self) -> None:
        """Test microcompact with empty messages."""
        pipeline = CompressionPipeline()

        result = pipeline.microcompact([])

        assert result.compressed_count == 0

    def test_autocompact_short_conversation(self) -> None:
        """Test autocompact with short conversation returns unchanged."""
        pipeline = CompressionPipeline()
        messages = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi!"},
        ]

        result = pipeline.autocompact(messages, recent_window=6)

        assert result.level_applied == "none"

    def test_autocompact_creates_summary(self) -> None:
        """Test that autocompact creates a summary of old messages."""
        pipeline = CompressionPipeline()
        messages = [
            {"role": "user", "content": "Question 1"},
            {"role": "assistant", "content": "Answer 1"},
            {"role": "user", "content": "Question 2"},
            {"role": "assistant", "content": "Answer 2"},
            {"role": "user", "content": "Question 3"},
            {"role": "assistant", "content": "Answer 3"},
        ]

        result = pipeline.autocompact(messages, recent_window=2)

        # Should have summary + recent messages
        assert result.level_applied == "autocompact"
        assert result.compressed_count < len(messages)

        # First message should be a summary
        assert "[Conversation summary]" in result.messages[0]["content"]
