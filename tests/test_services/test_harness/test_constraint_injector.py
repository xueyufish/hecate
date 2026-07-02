"""Unit tests for ConstraintInjector."""

from __future__ import annotations

from hecate.services.harness.constraint_generator import (
    ConstraintPriority,
    ConstraintRule,
)
from hecate.services.harness.constraint_injector import ConstraintInjector


class TestConstraintInjector:
    """Tests for the ConstraintInjector class."""

    def test_inject_constraints_into_system(self) -> None:
        """Test injecting constraints into existing system message."""
        injector = ConstraintInjector()

        messages = [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "Hello"},
        ]

        constraints = [
            ConstraintRule(
                id="c1",
                trigger="test",
                action="Always verify before acting",
                priority=ConstraintPriority.HIGH,
                source_failure="test",
            ),
        ]

        result = injector.inject_constraints(messages, constraints)

        assert len(result) == 2
        assert "Constraints" in result[0]["content"]
        assert "Always verify before acting" in result[0]["content"]

    def test_inject_constraints_no_system(self) -> None:
        """Test injecting constraints when no system message exists."""
        injector = ConstraintInjector()

        messages = [
            {"role": "user", "content": "Hello"},
        ]

        constraints = [
            ConstraintRule(
                id="c1",
                trigger="test",
                action="Be careful",
                priority=ConstraintPriority.HIGH,
                source_failure="test",
            ),
        ]

        result = injector.inject_constraints(messages, constraints)

        assert len(result) == 2
        assert result[0]["role"] == "system"
        assert "Be careful" in result[0]["content"]

    def test_inject_empty_constraints(self) -> None:
        """Test injecting empty constraints returns original messages."""
        injector = ConstraintInjector()

        messages = [
            {"role": "user", "content": "Hello"},
        ]

        result = injector.inject_constraints(messages, [])

        assert result == messages

    def test_inject_sorted_by_priority(self) -> None:
        """Test constraints are sorted by priority."""
        injector = ConstraintInjector()

        messages = [
            {"role": "system", "content": "System"},
        ]

        constraints = [
            ConstraintRule(
                id="c1",
                trigger="test",
                action="Low priority",
                priority=ConstraintPriority.LOW,
                source_failure="test",
            ),
            ConstraintRule(
                id="c2",
                trigger="test",
                action="High priority",
                priority=ConstraintPriority.HIGH,
                source_failure="test",
            ),
        ]

        result = injector.inject_constraints(messages, constraints)

        content = result[0]["content"]
        high_pos = content.find("High priority")
        low_pos = content.find("Low priority")

        assert high_pos < low_pos
