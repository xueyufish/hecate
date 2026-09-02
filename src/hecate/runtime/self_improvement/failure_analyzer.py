"""Failure analyzer for LLM-driven failure classification.

Classifies failures into 10 types (reference AgentRx taxonomy) and
performs root cause analysis on conversation trajectories.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

logger = logging.getLogger(__name__)


class FailureType(StrEnum):
    """Failure types based on AgentRx taxonomy."""

    INSTRUCTION_ADHERENCE = "instruction_adherence"
    INFORMATION_INVENTION = "information_invention"
    INVALID_INVOCATION = "invalid_invocation"
    TOOL_OUTPUT_MISINTERPRETATION = "tool_output_misinterpretation"
    INTENT_PLAN_MISALIGNMENT = "intent_plan_misalignment"
    UNDERSPECIFIED_INTENT = "underspecified_intent"
    UNSUPPORTED_INTENT = "unsupported_intent"
    GUARDRAILS_TRIGGERED = "guardrails_triggered"
    SYSTEM_FAILURE = "system_failure"
    INCONCLUSIVE = "inconclusive"


@dataclass
class FailureAnalysis:
    """Result of failure analysis."""

    failure_type: FailureType
    confidence: float
    description: str
    root_cause: str
    evidence: list[str]
    suggested_constraints: list[str]


class FailureAnalyzer:
    """Analyzes conversation trajectories to identify failure patterns.

    Provides:
    - Failure type classification (10 types from AgentRx)
    - Root cause analysis
    - Constraint suggestions for prevention
    """

    def classify_failure(
        self,
        trajectory: list[dict[str, Any]],
    ) -> FailureAnalysis:
        """Classify a failure from a conversation trajectory.

        Args:
            trajectory: List of messages in the conversation.

        Returns:
            FailureAnalysis with classification and root cause.
        """
        if not trajectory:
            return FailureAnalysis(
                failure_type=FailureType.INCONCLUSIVE,
                confidence=0.0,
                description="Empty trajectory",
                root_cause="No messages to analyze",
                evidence=[],
                suggested_constraints=[],
            )

        # Analyze trajectory patterns
        has_tool_error = self._has_tool_error(trajectory)
        has_user_correction = self._has_user_correction(trajectory)
        has_loop = self._has_repeated_pattern(trajectory)

        # Classify based on patterns
        if has_tool_error:
            return self._classify_tool_error(trajectory)
        elif has_user_correction:
            return self._classify_user_correction(trajectory)
        elif has_loop:
            return self._classify_loop(trajectory)
        else:
            return self._classify_general(trajectory)

    def analyze_root_cause(
        self,
        trajectory: list[dict[str, Any]],
    ) -> str:
        """Analyze root cause from trajectory.

        Args:
            trajectory: List of messages in the conversation.

        Returns:
            Root cause description string.
        """
        analysis = self.classify_failure(trajectory)
        return analysis.root_cause

    def _has_tool_error(self, trajectory: list[dict[str, Any]]) -> bool:
        """Check if trajectory contains tool errors."""
        return any(msg.get("role") == "tool" and msg.get("is_error") for msg in trajectory)

    def _has_user_correction(self, trajectory: list[dict[str, Any]]) -> bool:
        """Check if user corrected the agent."""
        correction_indicators = [
            "no, i meant",
            "that's wrong",
            "not what i asked",
            "incorrect",
            "try again",
        ]
        for msg in trajectory:
            if msg.get("role") == "user":
                content = str(msg.get("content", "")).lower()
                if any(indicator in content for indicator in correction_indicators):
                    return True
        return False

    def _has_repeated_pattern(self, trajectory: list[dict[str, Any]]) -> bool:
        """Check for repeated patterns (loops)."""
        if len(trajectory) < 4:
            return False

        # Check if last 4 messages have similar content
        recent = trajectory[-4:]
        contents = [str(m.get("content", ""))[:100] for m in recent]
        return len(set(contents)) < 2

    def _classify_tool_error(
        self,
        trajectory: list[dict[str, Any]],
    ) -> FailureAnalysis:
        """Classify tool error failures."""
        error_msgs = [m for m in trajectory if m.get("role") == "tool" and m.get("is_error")]

        return FailureAnalysis(
            failure_type=FailureType.INVALID_INVOCATION,
            confidence=0.8,
            description=f"Tool execution failed with {len(error_msgs)} errors",
            root_cause="Tool invocation error - incorrect arguments or tool unavailable",
            evidence=[str(m.get("content", ""))[:200] for m in error_msgs[:3]],
            suggested_constraints=[
                "Verify tool arguments before calling",
                "Check tool availability before invocation",
            ],
        )

    def _classify_user_correction(
        self,
        trajectory: list[dict[str, Any]],
    ) -> FailureAnalysis:
        """Classify user correction failures."""
        return FailureAnalysis(
            failure_type=FailureType.INTENT_PLAN_MISALIGNMENT,
            confidence=0.7,
            description="User corrected agent's understanding",
            root_cause="Agent misunderstood user intent or requirements",
            evidence=[],
            suggested_constraints=[
                "Clarify requirements before proceeding",
                "Confirm understanding with user",
            ],
        )

    def _classify_loop(
        self,
        trajectory: list[dict[str, Any]],
    ) -> FailureAnalysis:
        """Classify loop/repetition failures."""
        return FailureAnalysis(
            failure_type=FailureType.INSTRUCTION_ADHERENCE,
            confidence=0.75,
            description="Agent entered a repetitive loop",
            root_cause="Agent unable to make progress, repeating similar actions",
            evidence=[],
            suggested_constraints=[
                "Avoid repeating failed approaches",
                "Try alternative strategies after failure",
            ],
        )

    def _classify_general(
        self,
        trajectory: list[dict[str, Any]],
    ) -> FailureAnalysis:
        """Classify general failures."""
        return FailureAnalysis(
            failure_type=FailureType.INCONCLUSIVE,
            confidence=0.5,
            description="Unable to determine specific failure type",
            root_cause="Insufficient evidence for classification",
            evidence=[],
            suggested_constraints=[],
        )
