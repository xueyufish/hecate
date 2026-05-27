"""Provider-specific context shaping strategies.

Adapts assembled context to target LLM provider requirements:
- OpenAI: System message in messages array, standard tool format
- Anthropic: System message as top-level parameter, native tool format
- Default: Pass-through, no modifications

Strategies are selected automatically based on model name prefix and
can be extended via registration API.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any

from hecate.services.context.token_counter import TokenCounter
from hecate.services.context.types import AssembledContext

logger = logging.getLogger(__name__)

# OpenAI system message truncation threshold
_OPENAI_SYSTEM_MSG_MAX_TOKENS = 2_000


class ProviderStrategy(ABC):
    """Abstract base class for provider-specific context shaping.

    Each provider has different requirements for:
    - System message placement and length
    - Tool definition format
    - Message structure
    """

    @abstractmethod
    def shape(self, context: AssembledContext) -> AssembledContext:
        """Shape the context for the target provider.

        Args:
            context: Assembled context from ContextAssembler.

        Returns:
            Shaped context optimized for the provider.
        """
        ...

    @abstractmethod
    def get_system_param(self, context: AssembledContext) -> str | None:
        """Extract system message as a top-level parameter (if provider requires it).

        Args:
            context: Assembled context.

        Returns:
            System message string, or None if not needed.
        """
        ...


class DefaultStrategy(ProviderStrategy):
    """Default pass-through strategy for unknown providers.

    Makes no modifications - assumes OpenAI-compatible format.
    """

    def shape(self, context: AssembledContext) -> AssembledContext:
        """Pass-through: no modifications.

        Args:
            context: Assembled context.

        Returns:
            Context unchanged.
        """
        return context

    def get_system_param(self, context: AssembledContext) -> str | None:
        """No top-level system parameter needed.

        Args:
            context: Assembled context.

        Returns:
            None (system message stays in messages array).
        """
        return None


class OpenAIStrategy(ProviderStrategy):
    """Strategy for OpenAI models (gpt-4o, gpt-4, gpt-3.5-turbo).

    Requirements:
    - System message stays in messages array as first element
    - Long system messages are truncated to _OPENAI_SYSTEM_MSG_MAX_TOKENS
    - Standard OpenAI tool format (no conversion needed)
    """

    def __init__(self, token_counter: TokenCounter | None = None) -> None:
        """Initialize the OpenAI strategy.

        Args:
            token_counter: Optional token counter for truncation.
        """
        self.token_counter = token_counter or TokenCounter("gpt-4o")

    def shape(self, context: AssembledContext) -> AssembledContext:
        """Shape context for OpenAI models.

        Truncates long system messages and ensures proper message format.

        Args:
            context: Assembled context.

        Returns:
            Context shaped for OpenAI.
        """
        messages = list(context.messages)

        # Find and truncate long system messages
        for i, msg in enumerate(messages):
            if msg.get("role") == "system":
                content = msg.get("content", "")
                if isinstance(content, str):
                    token_count = self.token_counter.count_text(content)
                    if token_count > _OPENAI_SYSTEM_MSG_MAX_TOKENS:
                        # Truncate to fit within limit
                        truncated = self._truncate_text(content, _OPENAI_SYSTEM_MSG_MAX_TOKENS)
                        messages[i] = {
                            **msg,
                            "content": f"{truncated}\n[System message truncated due to length]",
                        }
                        logger.debug(
                            f"Truncated system message: {token_count} → ~{_OPENAI_SYSTEM_MSG_MAX_TOKENS} tokens"
                        )

        return AssembledContext(
            messages=messages,
            tools=context.tools,
            knowledge=context.knowledge,
            phase=context.phase,
            total_tokens=context.total_tokens,
            priorities=context.priorities,
            metadata={**context.metadata, "provider": "openai"},
        )

    def get_system_param(self, context: AssembledContext) -> str | None:
        """No top-level system parameter - stays in messages array.

        Args:
            context: Assembled context.

        Returns:
            None.
        """
        return None

    def _truncate_text(self, text: str, max_tokens: int) -> str:
        """Truncate text to approximately max_tokens.

        Args:
            text: Text to truncate.
            max_tokens: Maximum tokens.

        Returns:
            Truncated text.
        """
        # Simple character-based truncation (approximate)
        chars_per_token = 4  # Rough estimate for English
        max_chars = max_tokens * chars_per_token
        if len(text) <= max_chars:
            return text
        return text[:max_chars]


class AnthropicStrategy(ProviderStrategy):
    """Strategy for Anthropic models (claude-3-5-sonnet, claude-3-opus, etc.).

    Requirements:
    - System message extracted to top-level `system` parameter
    - Tool definitions use `input_schema` instead of `parameters`
    - Messages should not contain system role
    """

    def shape(self, context: AssembledContext) -> AssembledContext:
        """Shape context for Anthropic models.

        Extracts system message and adapts tool format.

        Args:
            context: Assembled context.

        Returns:
            Context shaped for Anthropic.
        """
        messages = list(context.messages)
        tools = list(context.tools)

        # Remove system messages from messages array (will be extracted)
        messages = [msg for msg in messages if msg.get("role") != "system"]

        # Adapt tool definitions to Anthropic format
        adapted_tools = self._adapt_tools(tools)

        return AssembledContext(
            messages=messages,
            tools=adapted_tools,
            knowledge=context.knowledge,
            phase=context.phase,
            total_tokens=context.total_tokens,
            priorities=context.priorities,
            metadata={**context.metadata, "provider": "anthropic"},
        )

    def get_system_param(self, context: AssembledContext) -> str | None:
        """Extract system message as top-level parameter.

        Args:
            context: Assembled context.

        Returns:
            Concatenated system message content, or None.
        """
        system_parts = []
        for msg in context.messages:
            if msg.get("role") == "system":
                content = msg.get("content", "")
                if isinstance(content, str):
                    system_parts.append(content)

        return "\n\n".join(system_parts) if system_parts else None

    def _adapt_tools(self, tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Adapt tool definitions to Anthropic format.

        Converts OpenAI `parameters` to Anthropic `input_schema`.

        Args:
            tools: OpenAI-format tool definitions.

        Returns:
            Anthropic-format tool definitions.
        """
        adapted = []
        for tool in tools:
            if tool.get("type") == "function":
                func = tool.get("function", {})
                # Convert parameters to input_schema
                parameters = func.get("parameters", {})
                adapted_func = {
                    "name": func.get("name", ""),
                    "description": func.get("description", ""),
                    "input_schema": parameters,
                }
                adapted.append(
                    {
                        "type": "function",
                        "function": adapted_func,
                    }
                )
            else:
                adapted.append(tool)

        return adapted


# ============================================================
# Strategy Registry
# ============================================================

# Model prefix → strategy class mapping
_STRATEGY_REGISTRY: dict[str, type[ProviderStrategy]] = {
    "gpt-": OpenAIStrategy,
    "o1-": OpenAIStrategy,
    "claude-": AnthropicStrategy,
}


def register_strategy(model_prefix: str, strategy_class: type[ProviderStrategy]) -> None:
    """Register a custom provider strategy.

    Args:
        model_prefix: Model name prefix to match (e.g., "deepseek-").
        strategy_class: Strategy class to use for matching models.
    """
    _STRATEGY_REGISTRY[model_prefix] = strategy_class
    logger.info(f"Registered strategy {strategy_class.__name__} for prefix '{model_prefix}'")


def get_strategy(model: str) -> ProviderStrategy:
    """Get the appropriate strategy for a model.

    Args:
        model: Model identifier (e.g., "gpt-4o", "claude-3-5-sonnet").

    Returns:
        ProviderStrategy instance for the model.
    """
    model_lower = model.lower()

    for prefix, strategy_class in _STRATEGY_REGISTRY.items():
        if model_lower.startswith(prefix):
            return strategy_class()

    # Default strategy for unknown models
    return DefaultStrategy()
