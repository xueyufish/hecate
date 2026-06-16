"""Unit tests for provider shaping strategies."""

from __future__ import annotations

from hecate.services.context.provider_shaping import (
    AnthropicStrategy,
    DefaultStrategy,
    OpenAIStrategy,
    get_strategy,
    register_strategy,
)
from hecate.services.context.types import AssembledContext


class TestDefaultStrategy:
    """Tests for the DefaultStrategy class."""

    def test_shape_passthrough(self) -> None:
        """Test that default strategy passes context through unchanged."""
        strategy = DefaultStrategy()
        context = AssembledContext(
            messages=[{"role": "user", "content": "Hello"}],
            tools=[],
        )

        shaped = strategy.shape(context)
        assert shaped.messages == context.messages
        assert shaped.tools == context.tools

    def test_get_system_param_returns_none(self) -> None:
        """Test that default strategy returns None for system param."""
        strategy = DefaultStrategy()
        context = AssembledContext(
            messages=[{"role": "system", "content": "You are helpful"}],
            tools=[],
        )

        assert strategy.get_system_param(context) is None


class TestOpenAIStrategy:
    """Tests for the OpenAIStrategy class."""

    def test_shape_preserves_messages(self) -> None:
        """Test that OpenAI strategy preserves message structure."""
        strategy = OpenAIStrategy()
        context = AssembledContext(
            messages=[
                {"role": "system", "content": "You are helpful."},
                {"role": "user", "content": "Hello"},
            ],
            tools=[],
        )

        shaped = strategy.shape(context)
        assert len(shaped.messages) == 2
        assert shaped.metadata["provider"] == "openai"

    def test_shape_truncates_long_system_message(self) -> None:
        """Test that OpenAI strategy truncates long system messages."""
        strategy = OpenAIStrategy()

        # Create a very long system message
        long_content = "You are helpful. " * 1000
        context = AssembledContext(
            messages=[{"role": "system", "content": long_content}],
            tools=[],
        )

        shaped = strategy.shape(context)
        # System message should be truncated
        system_msg = shaped.messages[0]
        assert "[System message truncated" in system_msg["content"]

    def test_get_system_param_returns_none(self) -> None:
        """Test that OpenAI strategy returns None (stays in messages)."""
        strategy = OpenAIStrategy()
        context = AssembledContext(
            messages=[{"role": "system", "content": "You are helpful"}],
            tools=[],
        )

        assert strategy.get_system_param(context) is None


class TestAnthropicStrategy:
    """Tests for the AnthropicStrategy class."""

    def test_shape_removes_system_messages(self) -> None:
        """Test that Anthropic strategy removes system messages from array."""
        strategy = AnthropicStrategy()
        context = AssembledContext(
            messages=[
                {"role": "system", "content": "You are helpful."},
                {"role": "user", "content": "Hello"},
                {"role": "assistant", "content": "Hi!"},
            ],
            tools=[],
        )

        shaped = strategy.shape(context)
        # System messages should be removed
        roles = [m["role"] for m in shaped.messages]
        assert "system" not in roles
        assert shaped.metadata["provider"] == "anthropic"

    def test_shape_adapts_tool_format(self) -> None:
        """Test that Anthropic strategy adapts tool format."""
        strategy = AnthropicStrategy()
        tools = [
            {
                "type": "function",
                "function": {
                    "name": "get_weather",
                    "description": "Get weather",
                    "parameters": {
                        "type": "object",
                        "properties": {"location": {"type": "string"}},
                    },
                },
            }
        ]

        context = AssembledContext(
            messages=[{"role": "user", "content": "Hello"}],
            tools=tools,
        )

        shaped = strategy.shape(context)
        # Tool should have input_schema instead of parameters
        adapted_tool = shaped.tools[0]
        assert "input_schema" in adapted_tool["function"]
        assert "parameters" not in adapted_tool["function"]

    def test_get_system_param_extracts_system(self) -> None:
        """Test that Anthropic strategy extracts system message."""
        strategy = AnthropicStrategy()
        context = AssembledContext(
            messages=[
                {"role": "system", "content": "You are helpful."},
                {"role": "system", "content": "Be concise."},
                {"role": "user", "content": "Hello"},
            ],
            tools=[],
        )

        system_param = strategy.get_system_param(context)
        assert system_param is not None
        assert "You are helpful." in system_param
        assert "Be concise." in system_param

    def test_get_system_param_returns_none_when_no_system(self) -> None:
        """Test that Anthropic strategy returns None when no system messages."""
        strategy = AnthropicStrategy()
        context = AssembledContext(
            messages=[{"role": "user", "content": "Hello"}],
            tools=[],
        )

        assert strategy.get_system_param(context) is None


class TestStrategyRegistry:
    """Tests for strategy registry functions."""

    def test_get_strategy_openai(self) -> None:
        """Test strategy selection for OpenAI models."""
        strategy = get_strategy("gpt-4o")
        assert isinstance(strategy, OpenAIStrategy)

        strategy = get_strategy("gpt-3.5-turbo")
        assert isinstance(strategy, OpenAIStrategy)

    def test_get_strategy_anthropic(self) -> None:
        """Test strategy selection for Anthropic models."""
        strategy = get_strategy("claude-3-5-sonnet")
        assert isinstance(strategy, AnthropicStrategy)

        strategy = get_strategy("claude-3-opus")
        assert isinstance(strategy, AnthropicStrategy)

    def test_get_strategy_unknown(self) -> None:
        """Test strategy selection for unknown models."""
        strategy = get_strategy("qwen-plus")
        assert isinstance(strategy, DefaultStrategy)

        strategy = get_strategy("deepseek-chat")
        assert isinstance(strategy, DefaultStrategy)

    def test_register_custom_strategy(self) -> None:
        """Test registering a custom strategy."""

        class CustomStrategy(DefaultStrategy):
            pass

        register_strategy("deepseek-", CustomStrategy)
        strategy = get_strategy("deepseek-chat")
        assert isinstance(strategy, CustomStrategy)
