"""Constraint generator for creating rules from failure analysis.

Generates constraint rules that can be injected into system prompts
to prevent similar failures in future conversations.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import StrEnum

from hecate.services.harness.failure_analyzer import FailureAnalysis

logger = logging.getLogger(__name__)


class ConstraintPriority(StrEnum):
    """Priority levels for constraint rules."""

    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


@dataclass
class ConstraintRule:
    """A constraint rule for injection into system prompts."""

    id: str
    trigger: str
    action: str
    priority: ConstraintPriority
    source_failure: str
    created_at: str = ""


class ConstraintGenerator:
    """Generates constraint rules from failure analysis.

    Creates actionable constraints that can be injected into
    system prompts to prevent similar failures.
    """

    def generate_constraint(
        self,
        analysis: FailureAnalysis,
    ) -> ConstraintRule | None:
        """Generate a constraint rule from failure analysis.

        Args:
            analysis: Failure analysis result.

        Returns:
            ConstraintRule or None if no constraint needed.
        """
        if not analysis.suggested_constraints:
            return None

        # Determine priority based on failure type
        priority = self._determine_priority(analysis)

        # Create constraint from first suggestion
        constraint_text = analysis.suggested_constraints[0]

        return ConstraintRule(
            id=f"constraint-{analysis.failure_type.value}",
            trigger=f"When {analysis.failure_type.value} is detected",
            action=constraint_text,
            priority=priority,
            source_failure=analysis.description,
        )

    def generate_constraints(
        self,
        analyses: list[FailureAnalysis],
    ) -> list[ConstraintRule]:
        """Generate multiple constraints from analyses.

        Args:
            analyses: List of failure analyses.

        Returns:
            List of constraint rules.
        """
        constraints: list[ConstraintRule] = []

        for analysis in analyses:
            constraint = self.generate_constraint(analysis)
            if constraint:
                constraints.append(constraint)

        return constraints

    def _determine_priority(self, analysis: FailureAnalysis) -> ConstraintPriority:
        """Determine constraint priority from analysis.

        Args:
            analysis: Failure analysis.

        Returns:
            ConstraintPriority level.
        """
        # High confidence failures get higher priority
        if analysis.confidence >= 0.8:
            return ConstraintPriority.HIGH
        elif analysis.confidence >= 0.6:
            return ConstraintPriority.MEDIUM
        else:
            return ConstraintPriority.LOW
