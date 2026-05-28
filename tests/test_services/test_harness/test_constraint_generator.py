"""Unit tests for ConstraintGenerator."""

from __future__ import annotations

from hecate.services.harness.constraint_generator import (
    ConstraintGenerator,
    ConstraintPriority,
)
from hecate.services.harness.failure_analyzer import FailureAnalysis, FailureType


class TestConstraintGenerator:
    """Tests for the ConstraintGenerator class."""

    def test_generate_constraint(self) -> None:
        """Test generating a constraint from analysis."""
        generator = ConstraintGenerator()

        analysis = FailureAnalysis(
            failure_type=FailureType.INVALID_INVOCATION,
            confidence=0.8,
            description="Tool failed",
            root_cause="Bad arguments",
            evidence=[],
            suggested_constraints=["Verify arguments before calling"],
        )

        constraint = generator.generate_constraint(analysis)

        assert constraint is not None
        assert constraint.action == "Verify arguments before calling"
        assert constraint.priority == ConstraintPriority.HIGH

    def test_generate_constraint_no_suggestions(self) -> None:
        """Test generating constraint with no suggestions returns None."""
        generator = ConstraintGenerator()

        analysis = FailureAnalysis(
            failure_type=FailureType.INCONCLUSIVE,
            confidence=0.5,
            description="Unknown",
            root_cause="Unknown",
            evidence=[],
            suggested_constraints=[],
        )

        constraint = generator.generate_constraint(analysis)

        assert constraint is None

    def test_generate_constraints_multiple(self) -> None:
        """Test generating multiple constraints."""
        generator = ConstraintGenerator()

        analyses = [
            FailureAnalysis(
                failure_type=FailureType.INVALID_INVOCATION,
                confidence=0.8,
                description="Error 1",
                root_cause="Cause 1",
                evidence=[],
                suggested_constraints=["Constraint 1"],
            ),
            FailureAnalysis(
                failure_type=FailureType.INTENT_PLAN_MISALIGNMENT,
                confidence=0.7,
                description="Error 2",
                root_cause="Cause 2",
                evidence=[],
                suggested_constraints=["Constraint 2"],
            ),
        ]

        constraints = generator.generate_constraints(analyses)

        assert len(constraints) == 2

    def test_priority_determination(self) -> None:
        """Test priority is determined by confidence."""
        generator = ConstraintGenerator()

        high = FailureAnalysis(
            failure_type=FailureType.INVALID_INVOCATION,
            confidence=0.9,
            description="High",
            root_cause="Cause",
            evidence=[],
            suggested_constraints=["Test"],
        )
        assert generator.generate_constraint(high).priority == ConstraintPriority.HIGH

        medium = FailureAnalysis(
            failure_type=FailureType.INVALID_INVOCATION,
            confidence=0.7,
            description="Medium",
            root_cause="Cause",
            evidence=[],
            suggested_constraints=["Test"],
        )
        assert generator.generate_constraint(medium).priority == ConstraintPriority.MEDIUM

        low = FailureAnalysis(
            failure_type=FailureType.INVALID_INVOCATION,
            confidence=0.4,
            description="Low",
            root_cause="Cause",
            evidence=[],
            suggested_constraints=["Test"],
        )
        assert generator.generate_constraint(low).priority == ConstraintPriority.LOW
