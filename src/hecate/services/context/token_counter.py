"""Token counter for context budget management.

Wraps tiktoken to provide accurate token counting for OpenAI models and
approximate counting for other providers. Used by BudgetManager to enforce
context window limits.
"""

from __future__ import annotations

import logging
from typing import Any

import tiktoken

logger = logging.getLogger(__name__)

# Default encoding for GPT-4/4o/3.5-turbo series
_DEFAULT_ENCODING = "cl100k_base"

# Model-specific encoding overrides
_MODEL_ENCODINGS: dict[str, str] = {
    "gpt-4o": "o200k_base",
    "gpt-4o-mini": "o200k_base",
}

# Approximate tokens per character for non-OpenAI models (fallback)
_FALLBACK_CHARS_PER_TOKEN = 4


class TokenCounter:
    """Count tokens in messages and text content.

    Uses tiktoken for OpenAI models with exact counting. For non-OpenAI models,
    falls back to approximate counting based on character length.
    """

    def __init__(self, model: str = "gpt-4o") -> None:
        """Initialize the token counter for a specific model.

        Args:
            model: The model identifier to determine encoding.
        """
        self.model = model
        self._encoder: tiktoken.Encoding | None = None
        self._init_encoder()

    def _init_encoder(self) -> None:
        """Initialize the tiktoken encoder for the model."""
        try:
            encoding_name = _MODEL_ENCODINGS.get(self.model, _DEFAULT_ENCODING)
            self._encoder = tiktoken.get_encoding(encoding_name)
        except Exception as e:
            logger.warning(f"Failed to load tiktoken encoder for {self.model}: {e}. Using fallback.")
            self._encoder = None

    def count_text(self, text: str) -> int:
        """Count tokens in a text string.

        Args:
            text: The text to count tokens for.

        Returns:
            Number of tokens.
        """
        if self._encoder is not None:
            return len(self._encoder.encode(text))
        # Fallback: approximate by character count
        return max(1, len(text) // _FALLBACK_CHARS_PER_TOKEN)

    def count_message(self, message: dict[str, Any]) -> int:
        """Count tokens in a single message.

        Accounts for message structure overhead (role, separators, etc.).

        Args:
            message: A message dict with 'role' and 'content' keys.

        Returns:
            Number of tokens including message overhead.
        """
        # Base overhead per message (role, separators)
        tokens = 4

        # Count role tokens
        role = message.get("role", "user")
        tokens += self.count_text(role)

        # Count content tokens
        content = message.get("content", "")
        if isinstance(content, str):
            tokens += self.count_text(content)
        elif isinstance(content, list):
            # Handle multimodal content (list of parts)
            for part in content:
                if isinstance(part, dict):
                    if part.get("type") == "text":
                        tokens += self.count_text(part.get("text", ""))
                    elif part.get("type") == "image_url":
                        # Approximate image tokens
                        tokens += 85

        # Count tool call tokens if present
        tool_calls = message.get("tool_calls")
        if tool_calls:
            for tc in tool_calls:
                if isinstance(tc, dict):
                    # Function name
                    func = tc.get("function", {})
                    tokens += self.count_text(func.get("name", ""))
                    # Arguments
                    tokens += self.count_text(func.get("arguments", ""))

        # Count tool_call_id tokens if present
        tool_call_id = message.get("tool_call_id")
        if tool_call_id:
            tokens += self.count_text(str(tool_call_id))

        return tokens

    def count_messages(self, messages: list[dict[str, Any]]) -> int:
        """Count total tokens across all messages.

        Args:
            messages: List of message dicts.

        Returns:
            Total token count including all messages.
        """
        total = 0
        for msg in messages:
            total += self.count_message(msg)
        # Add reply priming tokens
        total += 2
        return total

    def count_tool_definitions(self, tools: list[dict[str, Any]] | None) -> int:
        """Count tokens in tool definitions.

        Args:
            tools: List of tool definition dicts (OpenAI format).

        Returns:
            Total token count for tool definitions.
        """
        if not tools:
            return 0

        total = 0
        for tool in tools:
            # Tool type and function wrapper
            total += 10
            func = tool.get("function", {})
            # Function name
            total += self.count_text(func.get("name", ""))
            # Description
            total += self.count_text(func.get("description", ""))
            # Parameters schema
            params = func.get("parameters", {})
            total += self.count_text(str(params))

        return total
