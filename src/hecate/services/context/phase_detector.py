"""Task phase detection from conversation patterns.

Classifies the current conversation state into one of four phases:
- EXPLORE: Initial investigation, gathering information
- CONVERGE: Narrowing down, focusing on specific solutions
- EXECUTE: Active implementation, tool calls, code changes
- VERIFY: Checking results, testing, validation

Phase detection enables dynamic tool filtering and context optimization.
"""

from __future__ import annotations

import logging
from typing import Any

from hecate.services.context.types import TaskPhase

logger = logging.getLogger(__name__)

# Minimum messages needed for phase detection
_MIN_MESSAGES_FOR_DETECTION = 2


class PhaseDetector:
    """Detects the current task phase from conversation patterns.

    Analyzes recent message patterns to determine what phase of work
    the user is in, enabling context-appropriate tool filtering.
    """

    def __init__(self, lookback_window: int = 5) -> None:
        """Initialize the phase detector.

        Args:
            lookback_window: Number of recent messages to analyze.
        """
        self.lookback_window = lookback_window

    def detect_phase(
        self,
        messages: list[dict[str, Any]],
    ) -> TaskPhase:
        """Detect the current task phase from message history.

        Args:
            messages: Full conversation message history.

        Returns:
            Detected TaskPhase.
        """
        if len(messages) < _MIN_MESSAGES_FOR_DETECTION:
            return TaskPhase.EXPLORE

        # Get recent messages for analysis
        recent = messages[-self.lookback_window :]

        # Analyze patterns
        has_questions = self._has_questions(recent)
        has_tool_calls = self._has_tool_calls(recent)
        has_file_operations = self._has_file_operations(recent)
        has_search_operations = self._has_search_operations(recent)
        has_verification = self._has_verification(recent)

        # Phase detection logic
        if has_verification:
            return TaskPhase.VERIFY

        if has_file_operations and not has_questions:
            return TaskPhase.EXECUTE

        if has_search_operations and has_questions:
            return TaskPhase.EXPLORE

        if has_tool_calls and not has_questions:
            return TaskPhase.CONVERGE

        # Default to explore for new conversations
        return TaskPhase.EXPLORE

    def _has_questions(self, messages: list[dict[str, Any]]) -> bool:
        """Check if messages contain questions.

        Args:
            messages: Messages to analyze.

        Returns:
            True if questions are detected.
        """
        question_indicators = ["?", "how", "what", "why", "where", "when", "which", "can you"]
        for msg in messages:
            content = msg.get("content", "")
            if isinstance(content, str):
                content_lower = content.lower()
                if any(indicator in content_lower for indicator in question_indicators):
                    return True
        return False

    def _has_tool_calls(self, messages: list[dict[str, Any]]) -> bool:
        """Check if messages contain tool calls.

        Args:
            messages: Messages to analyze.

        Returns:
            True if tool calls are detected.
        """
        for msg in messages:
            if msg.get("role") == "assistant" and msg.get("tool_calls"):
                return True
            if msg.get("role") == "tool":
                return True
        return False

    def _has_file_operations(self, messages: list[dict[str, Any]]) -> bool:
        """Check if messages contain file operations.

        Args:
            messages: Messages to analyze.

        Returns:
            True if file operations are detected.
        """
        file_indicators = ["write", "edit", "create", "modify", "save", "update"]
        for msg in messages:
            content = msg.get("content", "")
            if isinstance(content, str):
                content_lower = content.lower()
                if any(indicator in content_lower for indicator in file_indicators) and (
                    "/" in content or "\\" in content or ".py" in content or ".ts" in content
                ):
                    return True
        return False

    def _has_search_operations(self, messages: list[dict[str, Any]]) -> bool:
        """Check if messages contain search operations.

        Args:
            messages: Messages to analyze.

        Returns:
            True if search operations are detected.
        """
        search_indicators = ["search", "find", "grep", "look for", "locate", "where is"]
        for msg in messages:
            content = msg.get("content", "")
            if isinstance(content, str):
                content_lower = content.lower()
                if any(indicator in content_lower for indicator in search_indicators):
                    return True
        return False

    def _has_verification(self, messages: list[dict[str, Any]]) -> bool:
        """Check if messages contain verification activities.

        Args:
            messages: Messages to analyze.

        Returns:
            True if verification is detected.
        """
        verify_indicators = ["test", "verify", "check", "validate", "confirm", "does it work", "is it correct"]
        for msg in messages:
            content = msg.get("content", "")
            if isinstance(content, str):
                content_lower = content.lower()
                if any(indicator in content_lower for indicator in verify_indicators):
                    return True
        return False
