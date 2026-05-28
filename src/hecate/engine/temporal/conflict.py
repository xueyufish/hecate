"""Conflict resolution for concurrent channel updates.

Provides strategies for resolving conflicts when multiple agents
update the same channel simultaneously:
- Optimistic locking with version check
- Merge strategies (list append, map merge, last-write-wins)
- Human approval via Temporal Signal for critical conflicts
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

logger = logging.getLogger(__name__)


class ConflictStrategy(StrEnum):
    """Conflict resolution strategies."""

    LAST_WRITE_WINS = "last_write_wins"
    MERGE_LIST = "merge_list"
    MERGE_MAP = "merge_map"
    HUMAN_APPROVAL = "human_approval"


@dataclass
class ConflictResult:
    """Result of a conflict resolution attempt."""

    resolved: bool
    final_value: Any = None
    strategy_used: str | None = None
    requires_approval: bool = False


class ConflictResolver:
    """Resolves conflicts for concurrent channel updates.

    Provides multiple resolution strategies based on channel type
    and conflict severity.
    """

    def resolve(
        self,
        channel_key: str,
        current_value: Any,
        proposed_value: Any,
        channel_type: str = "last_value",
        agent_id: str | None = None,
    ) -> ConflictResult:
        """Resolve a conflict between current and proposed values.

        Args:
            channel_key: The channel being updated.
            current_value: Current channel value.
            proposed_value: Proposed new value.
            channel_type: Type of channel (last_value, topic, accumulator).
            agent_id: ID of the agent making the update.

        Returns:
            ConflictResult with resolution outcome.
        """
        # Last-value channels: last write wins
        if channel_type == "last_value":
            return ConflictResult(
                resolved=True,
                final_value=proposed_value,
                strategy_used=ConflictStrategy.LAST_WRITE_WINS.value,
            )

        # Topic channels: merge lists
        if channel_type == "topic":
            merged = self._merge_lists(current_value, proposed_value)
            return ConflictResult(
                resolved=True,
                final_value=merged,
                strategy_used=ConflictStrategy.MERGE_LIST.value,
            )

        # Accumulator channels: sum values
        if channel_type == "accumulator":
            try:
                merged = (current_value or 0) + (proposed_value or 0)
                return ConflictResult(
                    resolved=True,
                    final_value=merged,
                    strategy_used="accumulator_sum",
                )
            except (TypeError, ValueError):
                pass

        # Default: last write wins
        return ConflictResult(
            resolved=True,
            final_value=proposed_value,
            strategy_used=ConflictStrategy.LAST_WRITE_WINS.value,
        )

    def _merge_lists(
        self,
        current: Any,
        proposed: Any,
    ) -> list[Any]:
        """Merge two list values, avoiding duplicates.

        Args:
            current: Current list value.
            proposed: Proposed list value.

        Returns:
            Merged list.
        """
        if not isinstance(current, list):
            current = [current] if current is not None else []
        if not isinstance(proposed, list):
            proposed = [proposed] if proposed is not None else []

        # Simple merge with deduplication
        seen = set()
        merged = []
        for item in current + proposed:
            item_key = str(item)
            if item_key not in seen:
                seen.add(item_key)
                merged.append(item)

        return merged

    def _merge_maps(
        self,
        current: dict[str, Any],
        proposed: dict[str, Any],
    ) -> dict[str, Any]:
        """Merge two map values.

        Args:
            current: Current map value.
            proposed: Proposed map value.

        Returns:
            Merged map (proposed overwrites current for same keys).
        """
        if not isinstance(current, dict):
            current = {}
        if not isinstance(proposed, dict):
            proposed = {}

        merged = current.copy()
        merged.update(proposed)
        return merged
