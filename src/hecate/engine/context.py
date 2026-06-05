"""Pluggable context management for message selection, compression, and token estimation.

Provides the abstract contract (ContextEngine) and a default implementation:
- ``InMemoryContextEngine`` — simple heuristics for testing and single-machine use

ContextEngine is the bottom layer for context operations. Higher-level
orchestration (WorkflowExecutionService) delegates to ContextEngine for the
fundamental context operations.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class ContextEngine(ABC):
    """Abstract interface for context management operations.

    A ContextEngine handles three fundamental operations:
    1. Select which messages to include given a token budget
    2. Compress messages to reduce token usage
    3. Estimate token count for messages
    """

    @abstractmethod
    def select_messages(self, history: list[dict[str, Any]], budget: int) -> list[dict[str, Any]]:
        """Select messages that fit within the token budget.

        Args:
            history: Full message history (oldest to newest).
            budget: Maximum token budget.

        Returns:
            Selected messages that fit within budget.
        """
        ...

    @abstractmethod
    def compress(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Compress messages to reduce token usage.

        Args:
            messages: Messages to compress.

        Returns:
            Compressed messages (fewer tokens).
        """
        ...

    @abstractmethod
    def estimate_tokens(self, messages: list[dict[str, Any]]) -> int:
        """Estimate the total token count for messages.

        Args:
            messages: Messages to estimate.

        Returns:
            Estimated token count.
        """
        ...


class InMemoryContextEngine(ContextEngine):
    """Simple context engine using heuristics for testing and single-machine use.

    Token estimation: approximately 4 characters per token.
    Message selection: keep most recent messages that fit budget.
    Compression: remove oldest messages beyond threshold.
    """

    def __init__(self, max_messages: int = 50, chars_per_token: int = 4) -> None:
        """Initialize with configuration.

        Args:
            max_messages: Maximum messages before compression triggers.
            chars_per_token: Characters per token for estimation.
        """
        self._max_messages = max_messages
        self._chars_per_token = chars_per_token

    def select_messages(self, history: list[dict[str, Any]], budget: int) -> list[dict[str, Any]]:
        """Select most recent messages that fit within token budget.

        Args:
            history: Full message history (oldest to newest).
            budget: Maximum token budget.

        Returns:
            Selected messages that fit within budget.
        """
        if not history or budget <= 0:
            return []

        selected: list[dict[str, Any]] = []
        token_count = 0

        for message in reversed(history):
            msg_tokens = self._estimate_single_message(message)
            if token_count + msg_tokens > budget:
                break
            selected.insert(0, message)
            token_count += msg_tokens

        return selected

    def compress(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Remove oldest messages when count exceeds threshold.

        Args:
            messages: Messages to compress.

        Returns:
            Compressed messages (newest max_messages).
        """
        if len(messages) <= self._max_messages:
            return list(messages)
        return messages[-self._max_messages :]

    def estimate_tokens(self, messages: list[dict[str, Any]]) -> int:
        """Estimate total token count using character-based heuristic.

        Args:
            messages: Messages to estimate.

        Returns:
            Estimated token count.
        """
        if not messages:
            return 0

        total_chars = sum(self._estimate_single_message_chars(msg) for msg in messages)
        return max(1, total_chars // self._chars_per_token)

    def _estimate_single_message(self, message: dict[str, Any]) -> int:
        """Estimate tokens for a single message.

        Args:
            message: Message dict.

        Returns:
            Estimated token count.
        """
        chars = self._estimate_single_message_chars(message)
        return max(1, chars // self._chars_per_token)

    def _estimate_single_message_chars(self, message: dict[str, Any]) -> int:
        """Count characters in a message.

        Args:
            message: Message dict.

        Returns:
            Total character count.
        """
        content = message.get("content", "")
        if content is None:
            return 0
        if isinstance(content, str):
            return len(content)
        return len(str(content))
