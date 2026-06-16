"""Budget manager for token budget governance.

Tracks per-session token budgets and enforces degradation strategies when
context exceeds allocated limits. Integrates with TokenCounter for accurate
token counting and provides structured degradation (DROP → COMPRESS → EMERGENCY).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any
from uuid import UUID

from hecate.services.context.token_counter import TokenCounter

logger = logging.getLogger(__name__)

# Default context window sizes by model family
_DEFAULT_BUDGETS: dict[str, int] = {
    "gpt-4o": 128_000,
    "gpt-4o-mini": 128_000,
    "gpt-4-turbo": 128_000,
    "gpt-4": 8_192,
    "gpt-3.5-turbo": 16_385,
    "claude-3-5-sonnet": 200_000,
    "claude-3-opus": 200_000,
    "claude-3-haiku": 200_000,
}

# Reserve tokens for generation output
_GENERATION_RESERVE = 1_024


# Priority levels for message degradation
class MessagePriority(StrEnum):
    """Priority levels for context degradation decisions."""

    CRITICAL = "critical"  # System prompt, current user message — never drop
    HIGH = "high"  # Recent exchanges, tool results — last to drop
    MEDIUM = "medium"  # Older messages — compress first
    LOW = "low"  # Early messages, notifications — drop first


class DegradationLevel(StrEnum):
    """Levels of context degradation."""

    NONE = "none"  # No degradation applied
    DROP = "drop"  # Level 1: Drop low-priority messages
    COMPRESS = "compress"  # Level 2: Compress medium-priority messages
    EMERGENCY = "emergency"  # Level 3: Replace all history with summary


@dataclass
class BudgetCheck:
    """Result of a budget check operation."""

    within_budget: bool
    total_tokens: int
    budget: int
    deficit: int  # How many tokens over budget (0 if within budget)
    degradation_level: DegradationLevel = DegradationLevel.NONE


@dataclass
class BudgetAllocation:
    """Tracks budget allocation for a session."""

    session_id: UUID
    total_budget: int
    tokens_used: int = 0
    degradation_events: list[DegradationLevel] = field(default_factory=list)

    @property
    def tokens_remaining(self) -> int:
        """Calculate remaining tokens in budget."""
        return max(0, self.total_budget - self.tokens_used)


class BudgetManager:
    """Manages token budgets per session with structured degradation.

    Features:
    - Per-session budget tracking
    - Three-level degradation strategy (DROP → COMPRESS → EMERGENCY)
    - Model-aware budget defaults
    - Budget usage reporting
    """

    def __init__(self, default_budget: int | None = None) -> None:
        """Initialize the budget manager.

        Args:
            default_budget: Default token budget if not model-specific.
                If None, uses model-specific defaults or 8192.
        """
        self.default_budget = default_budget
        self._allocations: dict[UUID, BudgetAllocation] = {}
        self._token_counters: dict[str, TokenCounter] = {}

    def _get_token_counter(self, model: str) -> TokenCounter:
        """Get or create a TokenCounter for the model."""
        if model not in self._token_counters:
            self._token_counters[model] = TokenCounter(model)
        return self._token_counters[model]

    def get_budget(self, model: str) -> int:
        """Get the budget for a model, considering model-specific defaults.

        Args:
            model: Model identifier.

        Returns:
            Token budget for the model.
        """
        if self.default_budget is not None:
            return self.default_budget

        # Check model-specific defaults
        for prefix, budget in _DEFAULT_BUDGETS.items():
            if model.startswith(prefix):
                return budget - _GENERATION_RESERVE

        # Fallback default
        return 8_192 - _GENERATION_RESERVE

    def allocate(self, session_id: UUID, model: str, custom_budget: int | None = None) -> BudgetAllocation:
        """Allocate a budget for a session.

        Args:
            session_id: The session to allocate budget for.
            model: Model identifier for budget calculation.
            custom_budget: Optional custom budget override.

        Returns:
            The budget allocation for the session.
        """
        budget = custom_budget if custom_budget is not None else self.get_budget(model)
        allocation = BudgetAllocation(
            session_id=session_id,
            total_budget=budget,
        )
        self._allocations[session_id] = allocation
        logger.info(f"Allocated budget of {budget} tokens for session {session_id}")
        return allocation

    def get_allocation(self, session_id: UUID) -> BudgetAllocation | None:
        """Get existing budget allocation for a session.

        Args:
            session_id: The session to get allocation for.

        Returns:
            BudgetAllocation if exists, None otherwise.
        """
        return self._allocations.get(session_id)

    def check_budget(
        self,
        session_id: UUID,
        messages: list[dict[str, Any]],
        model: str = "gpt-4o",
        tools: list[dict[str, Any]] | None = None,
    ) -> BudgetCheck:
        """Check if messages fit within the session budget.

        Args:
            session_id: The session to check budget for.
            messages: Messages to count tokens for.
            model: Model identifier for token counting.
            tools: Optional tool definitions to include in count.

        Returns:
            BudgetCheck with status and deficit information.
        """
        # Get or create allocation
        allocation = self._allocations.get(session_id)
        if allocation is None:
            allocation = self.allocate(session_id, model)

        # Count tokens
        counter = self._get_token_counter(model)
        message_tokens = counter.count_messages(messages)
        tool_tokens = counter.count_tool_definitions(tools)
        total_tokens = message_tokens + tool_tokens

        # Update usage
        allocation.tokens_used = total_tokens

        # Check if within budget
        if total_tokens <= allocation.total_budget:
            return BudgetCheck(
                within_budget=True,
                total_tokens=total_tokens,
                budget=allocation.total_budget,
                deficit=0,
            )

        # Calculate deficit
        deficit = total_tokens - allocation.total_budget
        return BudgetCheck(
            within_budget=False,
            total_tokens=total_tokens,
            budget=allocation.total_budget,
            deficit=deficit,
        )

    def update_usage(self, session_id: UUID, tokens_used: int) -> None:
        """Update token usage for a session after LLM call.

        Args:
            session_id: The session to update.
            tokens_used: Actual tokens used in the LLM call.
        """
        allocation = self._allocations.get(session_id)
        if allocation:
            allocation.tokens_used = tokens_used

    def record_degradation(self, session_id: UUID, level: DegradationLevel) -> None:
        """Record a degradation event for a session.

        Args:
            session_id: The session that experienced degradation.
            level: The degradation level applied.
        """
        allocation = self._allocations.get(session_id)
        if allocation:
            allocation.degradation_events.append(level)
            logger.info(f"Recorded {level.value} degradation for session {session_id}")

    def get_usage_report(self, session_id: UUID) -> dict[str, Any]:
        """Get budget usage report for a session.

        Args:
            session_id: The session to report on.

        Returns:
            Dictionary with budget usage metrics.
        """
        allocation = self._allocations.get(session_id)
        if allocation is None:
            return {
                "session_id": str(session_id),
                "allocated": 0,
                "used": 0,
                "remaining": 0,
                "degradation_events": [],
            }

        return {
            "session_id": str(session_id),
            "allocated": allocation.total_budget,
            "used": allocation.tokens_used,
            "remaining": allocation.tokens_remaining,
            "utilization": allocation.tokens_used / allocation.total_budget if allocation.total_budget > 0 else 0,
            "degradation_events": [e.value for e in allocation.degradation_events],
        }
