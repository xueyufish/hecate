"""Unit tests for TokenCounter (extended coverage)."""

from __future__ import annotations

from hecate.services.context.token_counter import TokenCounter


class TestTokenCounterExtended:
    """Extended tests for the TokenCounter class to improve coverage."""

    def test_count_message_with_tool_calls(self) -> None:
        """Test counting messages with tool calls."""
        counter = TokenCounter("gpt-4o")
        msg = {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_123",
                    "type": "function",
                    "function": {
                        "name": "get_weather",
                        "arguments": '{"location": "San Francisco"}',
                    },
                }
            ],
        }
        tokens = counter.count_message(msg)
        assert tokens > 0

    def test_count_message_with_tool_call_id(self) -> None:
        """Test counting messages with tool_call_id."""
        counter = TokenCounter("gpt-4o")
        msg = {
            "role": "tool",
            "content": "Weather is sunny",
            "tool_call_id": "call_123",
        }
        tokens = counter.count_message(msg)
        assert tokens > 0

    def test_count_message_multimodal_text(self) -> None:
        """Test counting multimodal messages with text parts."""
        counter = TokenCounter("gpt-4o")
        msg = {
            "role": "user",
            "content": [
                {"type": "text", "text": "What is in this image?"},
            ],
        }
        tokens = counter.count_message(msg)
        assert tokens > 0

    def test_count_message_multimodal_image(self) -> None:
        """Test counting multimodal messages with image parts."""
        counter = TokenCounter("gpt-4o")
        msg = {
            "role": "user",
            "content": [
                {"type": "text", "text": "Describe this"},
                {"type": "image_url", "image_url": {"url": "https://example.com/image.png"}},
            ],
        }
        tokens = counter.count_message(msg)
        assert tokens > 0

    def test_count_message_empty_content(self) -> None:
        """Test counting messages with empty content."""
        counter = TokenCounter("gpt-4o")
        msg = {"role": "user", "content": ""}
        tokens = counter.count_message(msg)
        # Should still have some overhead tokens
        assert tokens >= 0

    def test_count_message_none_content(self) -> None:
        """Test counting messages with None content."""
        counter = TokenCounter("gpt-4o")
        msg = {"role": "assistant", "content": None}
        tokens = counter.count_message(msg)
        assert tokens >= 0

    def test_count_tool_definitions_multiple(self) -> None:
        """Test counting multiple tool definitions."""
        counter = TokenCounter("gpt-4o")
        tools = [
            {
                "type": "function",
                "function": {
                    "name": "tool1",
                    "description": "First tool",
                    "parameters": {"type": "object", "properties": {}},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "tool2",
                    "description": "Second tool with longer description",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "arg1": {"type": "string", "description": "First argument"},
                            "arg2": {"type": "number", "description": "Second argument"},
                        },
                    },
                },
            },
        ]
        tokens = counter.count_tool_definitions(tools)
        assert tokens > 0

    def test_count_tool_definitions_no_parameters(self) -> None:
        """Test counting tool definitions without parameters."""
        counter = TokenCounter("gpt-4o")
        tools = [
            {
                "type": "function",
                "function": {
                    "name": "simple_tool",
                    "description": "A simple tool",
                },
            }
        ]
        tokens = counter.count_tool_definitions(tools)
        assert tokens > 0

    def test_count_messages_with_reply_priming(self) -> None:
        """Test that reply priming tokens are added."""
        counter = TokenCounter("gpt-4o")
        messages = [{"role": "user", "content": "Hello"}]
        tokens = counter.count_messages(messages)

        # Should include message tokens + 2 reply priming tokens
        message_tokens = counter.count_message(messages[0])
        assert tokens == message_tokens + 2

    def test_count_text_fallback_model(self) -> None:
        """Test text counting with fallback for unknown model."""
        counter = TokenCounter("unknown-model-xyz")
        # Should use fallback counting
        tokens = counter.count_text("Hello, world!")
        assert tokens > 0

    def test_count_text_fallback_empty(self) -> None:
        """Test fallback counting with empty text."""
        counter = TokenCounter("unknown-model-xyz")
        tokens = counter.count_text("")
        assert tokens >= 0

    def test_count_messages_fallback(self) -> None:
        """Test message counting with fallback model."""
        counter = TokenCounter("unknown-model-xyz")
        messages = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi!"},
        ]
        tokens = counter.count_messages(messages)
        assert tokens > 0

    def test_count_message_no_role(self) -> None:
        """Test counting message without explicit role."""
        counter = TokenCounter("gpt-4o")
        msg = {"content": "Hello"}
        tokens = counter.count_message(msg)
        # Should default to "user" role
        assert tokens > 0
