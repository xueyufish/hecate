"""Constraint injector for adding rules to system prompts.

Injects constraint rules into the system prompt before LLM calls
to prevent known failure patterns.
"""

from __future__ import annotations

import logging
from typing import Any

from hecate.services.harness.constraint_generator import ConstraintRule

logger = logging.getLogger(__name__)


class ConstraintInjector:
    """Injects constraint rules into system prompts.

    Appends constraints to the system message to guide LLM behavior
    and prevent known failure patterns.
    """

    def inject_constraints(
        self,
        messages: list[dict[str, Any]],
        constraints: list[ConstraintRule],
    ) -> list[dict[str, Any]]:
        """Inject constraints into message context.

        Args:
            messages: Original message list.
            constraints: Constraint rules to inject.

        Returns:
            Messages with constraints injected into system prompt.
        """
        if not constraints:
            return messages

        # Sort by priority
        sorted_constraints = sorted(
            constraints,
            key=lambda c: self._priority_order(c.priority),
        )

        # Build constraint text
        constraint_text = self._build_constraint_text(sorted_constraints)

        # Find system message or create one
        messages = list(messages)
        system_idx = self._find_system_message(messages)

        if system_idx >= 0:
            # Append to existing system message
            existing = messages[system_idx].get("content", "")
            messages[system_idx] = {
                "role": "system",
                "content": f"{existing}\n\n[Constraints]:\n{constraint_text}",
            }
        else:
            # Insert new system message at beginning
            messages.insert(
                0,
                {
                    "role": "system",
                    "content": f"[Constraints]:\n{constraint_text}",
                },
            )

        return messages

    def _build_constraint_text(self, constraints: list[ConstraintRule]) -> str:
        """Build constraint text from rules.

        Args:
            constraints: Sorted list of constraints.

        Returns:
            Formatted constraint text.
        """
        lines: list[str] = []
        for c in constraints:
            lines.append(f"- [{c.priority.value}] {c.action}")

        return "\n".join(lines)

    def _find_system_message(self, messages: list[dict[str, Any]]) -> int:
        """Find index of system message.

        Args:
            messages: Message list.

        Returns:
            Index of system message, or -1 if not found.
        """
        for i, msg in enumerate(messages):
            if msg.get("role") == "system":
                return i
        return -1

    def _priority_order(self, priority: str) -> int:
        """Get numeric order for priority sorting.

        Args:
            priority: Priority string.

        Returns:
            Numeric order (lower = higher priority).
        """
        order = {
            "critical": 0,
            "high": 1,
            "medium": 2,
            "low": 3,
        }
        return order.get(priority, 99)
