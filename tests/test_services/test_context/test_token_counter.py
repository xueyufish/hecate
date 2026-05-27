"""Unit tests for TokenCounter."""

from __future__ import annotations

from hecate.services.context.token_counter import TokenCounter


class TestTokenCounter:
    """Tests for the TokenCounter class."""

    def test_count_text_basic(self) -> None:
        """Test basic text token counting."""
        counter = TokenCounter("gpt-4o")
        tokens = counter.count_text("Hello, world!")
        assert tokens > 0
        assert tokens < 10  # Short text should be few tokens

    def test_count_text_empty(self) -> None:
        """Test empty text returns 0 or minimal tokens."""
        counter = TokenCounter("gpt-4o")
        tokens = counter.count_text("")
        assert tokens >= 0

    def test_count_text_long(self) -> None:
        """Test longer text produces more tokens."""
        counter = TokenCounter("gpt-4o")
        short = counter.count_text("Hello")
        long = counter.count_text("Hello " * 100)
        assert long > short

    def test_count_message_basic(self) -> None:
        """Test basic message token counting."""
        counter = TokenCounter("gpt-4o")
        msg = {"role": "user", "content": "Hello, how are you?"}
        tokens = counter.count_message(msg)
        assert tokens > 0

    def test_count_message_with_role_overhead(self) -> None:
        """Test that message counting includes role overhead."""
        counter = TokenCounter("gpt-4o")
        msg = {"role": "user", "content": "Hi"}
        tokens = counter.count_message(msg)
        # Should include role tokens + content tokens + overhead
        content_tokens = counter.count_text("Hi")
        assert tokens > content_tokens

    def test_count_messages_multiple(self) -> None:
        """Test counting multiple messages."""
        counter = TokenCounter("gpt-4o")
        messages = [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there!"},
        ]
        tokens = counter.count_messages(messages)
        assert tokens > 0

    def test_count_tool_definitions(self) -> None:
        """Test counting tool definitions."""
        counter = TokenCounter("gpt-4o")
        tools = [
            {
                "type": "function",
                "function": {
                    "name": "get_weather",
                    "description": "Get weather for a location",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "location": {"type": "string"},
                        },
                    },
                },
            }
        ]
        tokens = counter.count_tool_definitions(tools)
        assert tokens > 0

    def test_count_tool_definitions_empty(self) -> None:
        """Test counting empty tool definitions returns 0."""
        counter = TokenCounter("gpt-4o")
        assert counter.count_tool_definitions(None) == 0
        assert counter.count_tool_definitions([]) == 0

    def test_fallback_for_unknown_model(self) -> None:
        """Test fallback token counting for unknown models."""
        counter = TokenCounter("unknown-model-xyz")
        tokens = counter.count_text("Hello, world!")
        assert tokens > 0
