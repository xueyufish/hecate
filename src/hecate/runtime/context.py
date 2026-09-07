"""Pluggable context management for message selection, compression, and token estimation.

Provides the abstract contract (ContextEngine) and two implementations:
- ``InMemoryContextEngine`` — recency-window heuristics for testing and
  single-machine use
- ``PriorityContextEngine`` — importance-prioritized selection (4.12
  Message Prioritization): role/recency scoring with guaranteed retention
  of system messages and the newest user message

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
        """Count characters in a message dict.

        Args:
            message: The message dict.

        Returns:
            Total character count.
        """
        content = message.get("content", "")
        if content is None:
            return 0
        if isinstance(content, str):
            return len(content)
        return len(str(content))


class PriorityContextEngine(ContextEngine):
    """Importance-prioritized context engine (4.12 Message Prioritization).

    Selection scores every message by importance instead of taking a plain
    recency window, then keeps the highest-scoring subset that fits the
    budget while restoring the original conversation order. Always kept:
    system messages and the newest user message.

    Scoring (higher wins):

    - recency: ``index / len(history)`` — newest scores ~1.0
    - role bonus: system +0.5 (always kept anyway), user +0.3,
      assistant +0.1, tool +0.0
    - failed tool result penalty: −0.4 (diagnostic value, but low reuse)
    - long tool result penalty: over ``tool_result_soft_limit`` chars −0.2
    """

    def __init__(
        self,
        chars_per_token: int = 4,
        tool_result_soft_limit: int = 2_000,
    ) -> None:
        self._chars_per_token = chars_per_token
        self._tool_result_soft_limit = tool_result_soft_limit

    def select_messages(self, history: list[dict[str, Any]], budget: int) -> list[dict[str, Any]]:
        """Select the highest-importance messages that fit the budget.

        Args:
            history: Full message history (oldest to newest).
            budget: Maximum token budget.

        Returns:
            Selected messages in original order. Always includes system
            messages and the newest user message when the budget allows.
        """
        if not history or budget <= 0:
            return []

        messages = [m for m in history if isinstance(m, dict)]
        if not messages:
            return []

        scores = [self._score(i, m, len(messages)) for i, m in enumerate(messages)]
        always_keep = {i for i, m in enumerate(messages) if m.get("role") == "system"}
        newest_user = max(
            (i for i, m in enumerate(messages) if m.get("role") == "user"),
            default=None,
        )
        if newest_user is not None:
            always_keep.add(newest_user)

        # Walk candidates from highest score; keep while budget lasts.
        order = sorted(
            (i for i in range(len(messages)) if i not in always_keep),
            key=lambda i: scores[i],
            reverse=True,
        )
        selected = set(always_keep)
        used = sum(self._message_tokens(messages[i]) for i in selected)
        for i in order:
            tokens = self._message_tokens(messages[i])
            if used + tokens > budget:
                continue
            selected.add(i)
            used += tokens

        return [messages[i] for i in sorted(selected)]

    def compress(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Compress by keeping system messages and the scored top half.

        Args:
            messages: Messages to compress.

        Returns:
            The retained subset in original order (at least the most
            recent message survives).
        """
        if len(messages) <= 2:
            return list(messages)
        scores = [self._score(i, m, len(messages)) for i, m in enumerate(messages)]
        keep_count = max(2, len(messages) // 2)
        top = sorted(range(len(messages)), key=lambda i: scores[i], reverse=True)[:keep_count]
        return [messages[i] for i in sorted(top)]

    def estimate_tokens(self, messages: list[dict[str, Any]]) -> int:
        """Estimate total token count using the character heuristic."""
        if not messages:
            return 0
        total = sum(self._message_chars(m) for m in messages if isinstance(m, dict))
        return max(1, total // self._chars_per_token)

    def _score(self, index: int, message: dict[str, Any], total: int) -> float:
        """Compute the importance score for one message."""
        recency = index / total if total else 0.0
        role = message.get("role")
        role_bonus = {"system": 0.5, "user": 0.3, "assistant": 0.1}.get(role, 0.0)
        score = recency + role_bonus
        if role == "tool":
            content = message.get("content")
            text = content if isinstance(content, str) else str(content or "")
            lowered = text.lower()
            if any(m in lowered for m in ("error", "failed", "exception", "traceback")):
                score -= 0.4
            if len(text) > self._tool_result_soft_limit:
                score -= 0.2
        return score

    def _message_chars(self, message: dict[str, Any]) -> int:
        content = message.get("content", "")
        if content is None:
            return 0
        return len(content) if isinstance(content, str) else len(str(content))

    def _message_tokens(self, message: dict[str, Any]) -> int:
        return max(1, self._message_chars(message) // self._chars_per_token)
