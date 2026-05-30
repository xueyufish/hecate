"""Conversation compression pipeline for L2 memory.

Implements three-level compression to reduce context size:
- **snip**: Remove low-value messages, preserve recent N
- **microcompact**: Merge consecutive same-role messages
- **autocompact**: LLM summary of older messages

The pipeline is designed to integrate with ContextEngine for
automatic context management.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from hecate.services.context.token_counter import TokenCounter

logger = logging.getLogger(__name__)


@dataclass
class CompressionResult:
    """Result of a compression operation."""

    messages: list[dict[str, Any]]
    original_count: int
    compressed_count: int
    level_applied: str  # "none", "snip", "microcompact", "autocompact"
    tokens_saved: int


class CompressionPipeline:
    """Pipeline for compressing conversation history.

    Applies compression strategies in order:
    1. snip — remove low-value messages
    2. microcompact — merge consecutive same-role messages
    3. autocompact — LLM summary of older messages
    """

    def __init__(self, token_counter: TokenCounter | None = None) -> None:
        """Initialize the compression pipeline.

        Args:
            token_counter: Token counter for measuring compression效果.
        """
        self.token_counter = token_counter or TokenCounter()

    def snip(
        self,
        messages: list[dict[str, Any]],
        recent_window: int = 6,
    ) -> CompressionResult:
        """Remove low-value messages, preserving recent N.

        Removes system notification messages and old tool results
        while preserving the most recent exchanges.

        Args:
            messages: Full message history.
            recent_window: Number of recent messages to preserve.

        Returns:
            CompressionResult with compressed messages.
        """
        if len(messages) <= recent_window:
            return CompressionResult(
                messages=messages,
                original_count=len(messages),
                compressed_count=len(messages),
                level_applied="none",
                tokens_saved=0,
            )

        original_tokens = self.token_counter.count_messages(messages)

        # Split into old and recent
        old_messages = messages[:-recent_window]
        recent_messages = messages[-recent_window:]

        # Filter old messages - keep system messages and important ones
        kept_old = []
        for msg in old_messages:
            role = msg.get("role", "")

            # Always keep system messages
            if role == "system":
                kept_old.append(msg)
                continue

            # Skip tool results (they're usually not needed after context)
            if role == "tool":
                continue

            # Keep user and assistant messages
            if role in ("user", "assistant"):
                kept_old.append(msg)

        compressed = kept_old + recent_messages
        compressed_tokens = self.token_counter.count_messages(compressed)

        logger.debug(
            f"Snip: {len(messages)} → {len(compressed)} messages, {original_tokens} → {compressed_tokens} tokens"
        )

        return CompressionResult(
            messages=compressed,
            original_count=len(messages),
            compressed_count=len(compressed),
            level_applied="snip",
            tokens_saved=original_tokens - compressed_tokens,
        )

    def microcompact(
        self,
        messages: list[dict[str, Any]],
    ) -> CompressionResult:
        """Merge consecutive same-role messages.

        Combines adjacent messages from the same role into a single
        message with combined content.

        Args:
            messages: Message list (may have consecutive same-role messages).

        Returns:
            CompressionResult with merged messages.
        """
        if not messages:
            return CompressionResult(
                messages=[],
                original_count=0,
                compressed_count=0,
                level_applied="none",
                tokens_saved=0,
            )

        original_tokens = self.token_counter.count_messages(messages)
        merged: list[dict[str, Any]] = []
        current_role: str | None = None
        current_content_parts: list[str] = []

        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")

            if role == current_role:
                # Same role - accumulate content
                if content:
                    current_content_parts.append(content)
            else:
                # Different role - flush previous and start new
                if current_role is not None and current_content_parts:
                    merged.append(
                        {
                            "role": current_role,
                            "content": "\n\n".join(current_content_parts),
                        }
                    )
                current_role = role
                current_content_parts = [content] if content else []

        # Flush last group
        if current_role is not None and current_content_parts:
            merged.append(
                {
                    "role": current_role,
                    "content": "\n\n".join(current_content_parts),
                }
            )

        compressed_tokens = self.token_counter.count_messages(merged)

        logger.debug(
            f"Microcompact: {len(messages)} → {len(merged)} messages, {original_tokens} → {compressed_tokens} tokens"
        )

        return CompressionResult(
            messages=merged,
            original_count=len(messages),
            compressed_count=len(merged),
            level_applied="microcompact",
            tokens_saved=original_tokens - compressed_tokens,
        )

    def autocompact(
        self,
        messages: list[dict[str, Any]],
        recent_window: int = 6,
    ) -> CompressionResult:
        """Create a summary of older messages.

        Replaces older messages with a summary while preserving
        recent messages in full.

        Args:
            messages: Full message history.
            recent_window: Number of recent messages to preserve.

        Returns:
            CompressionResult with summary message.
        """
        if len(messages) <= recent_window:
            return CompressionResult(
                messages=messages,
                original_count=len(messages),
                compressed_count=len(messages),
                level_applied="none",
                tokens_saved=0,
            )

        original_tokens = self.token_counter.count_messages(messages)

        old_messages = messages[:-recent_window]
        recent_messages = messages[-recent_window:]

        # Create summary of old messages
        summary = self._create_summary(old_messages)
        summary_message = {
            "role": "system",
            "content": f"[Conversation summary]: {summary}",
        }

        compressed = [summary_message] + recent_messages
        compressed_tokens = self.token_counter.count_messages(compressed)

        logger.debug(
            f"Autocompact: {len(messages)} → {len(compressed)} messages, {original_tokens} → {compressed_tokens} tokens"
        )

        return CompressionResult(
            messages=compressed,
            original_count=len(messages),
            compressed_count=len(compressed),
            level_applied="autocompact",
            tokens_saved=original_tokens - compressed_tokens,
        )

    def compress(
        self,
        messages: list[dict[str, Any]],
        token_threshold: int = 4000,
        recent_window: int = 6,
    ) -> CompressionResult:
        """High-level compression chaining snip → microcompact → autocompact.

        Only compresses if token count exceeds the threshold. Applies
        strategies progressively until the result fits within budget.

        Args:
            messages: Full message history.
            token_threshold: Token count that triggers compression.
            recent_window: Messages to preserve unconditionally.

        Returns:
            CompressionResult with final compressed messages.
        """
        token_count = self.token_counter.count_messages(messages)
        if token_count <= token_threshold:
            return CompressionResult(
                messages=messages,
                original_count=len(messages),
                compressed_count=len(messages),
                level_applied="none",
                tokens_saved=0,
            )

        level_parts: list[str] = []
        current = messages

        result = self.snip(current, recent_window=recent_window)
        current = result.messages
        level_parts.append("snip")

        if self.token_counter.count_messages(current) > token_threshold:
            result = self.microcompact(current)
            current = result.messages
            level_parts.append("microcompact")

        if self.token_counter.count_messages(current) > token_threshold:
            result = self.autocompact(current, recent_window=recent_window)
            current = result.messages
            level_parts.append("autocompact")

        compressed_tokens = self.token_counter.count_messages(current)
        return CompressionResult(
            messages=current,
            original_count=len(messages),
            compressed_count=len(current),
            level_applied="+".join(level_parts),
            tokens_saved=token_count - compressed_tokens,
        )

    def _create_summary(self, messages: list[dict[str, Any]]) -> str:
        """Create a concise summary of messages.

        Args:
            messages: Messages to summarize.

        Returns:
            Summary string.
        """
        key_points: list[str] = []

        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")

            if not content:
                continue

            if role == "user":
                # Truncate user messages
                key_points.append(f"User: {content[:100]}")
            elif role == "assistant":
                # First sentence of assistant responses
                first_sentence = content.split(".")[0] if content else ""
                if first_sentence:
                    key_points.append(f"Assistant: {first_sentence[:100]}")
            elif role == "tool":
                # Note tool usage
                key_points.append("Tool result")

        if not key_points:
            return "Previous conversation context."

        # Combine into summary (max 5 points)
        summary = "; ".join(key_points[:5])
        if len(key_points) > 5:
            summary += f" ... and {len(key_points) - 5} more exchanges"

        return summary
