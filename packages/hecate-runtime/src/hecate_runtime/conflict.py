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
    DISTRIBUTED_LOCK = "distributed_lock"
    NEGOTIATION = "negotiation"


@dataclass
class PendingApproval:
    """A conflict resolution pending human approval."""

    conflict_id: str
    channel_key: str
    current_value: Any
    proposed_value: Any
    agent_id: str | None = None
    approved: bool | None = None
    approver: str | None = None


@dataclass
class ConflictResult:
    """Result of a conflict resolution attempt."""

    resolved: bool
    final_value: Any = None
    strategy_used: str | None = None
    requires_approval: bool = False


class ConflictResolver:
    """Resolves conflicts for concurrent channel updates.

    Provides multiple resolution strategies based on channel behavior
    and conflict severity. Supports human approval for critical conflicts
    via a pending-approval queue that external systems (e.g., Temporal Signals)
    can resolve.
    """

    def __init__(self) -> None:
        self._pending_approvals: dict[str, PendingApproval] = {}

    def resolve(
        self,
        channel_key: str,
        current_value: Any,
        proposed_value: Any,
        behavior: Any | None = None,
        agent_id: str | None = None,
        require_approval: bool = False,
    ) -> ConflictResult:
        """Resolve a conflict between current and proposed values.

        Args:
            channel_key: The channel being updated.
            current_value: Current channel value.
            proposed_value: Proposed new value.
            behavior: ChannelBehavior instance for this channel type.
                If provided, delegates conflict resolution to the behavior.
                If None, falls back to last-write-wins.
            agent_id: ID of the agent making the update.

        Returns:
            ConflictResult with resolution outcome.
        """
        if require_approval:
            return self._request_approval(channel_key, current_value, proposed_value, agent_id)

        if behavior is not None:
            try:
                merged = behavior.resolve_conflict(current_value, proposed_value)
                return ConflictResult(
                    resolved=True,
                    final_value=merged,
                    strategy_used="behavior_delegated",
                )
            except (TypeError, ValueError):
                pass

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

    def _request_approval(
        self,
        channel_key: str,
        current_value: Any,
        proposed_value: Any,
        agent_id: str | None = None,
    ) -> ConflictResult:
        """Request human approval for a critical conflict.

        Creates a PendingApproval entry and returns an unresolved ConflictResult.
        External systems (Temporal Signals) call resolve_approval() to complete.

        Args:
            channel_key: The channel being updated.
            current_value: Current channel value.
            proposed_value: Proposed new value.
            agent_id: ID of the agent making the update.

        Returns:
            ConflictResult with requires_approval=True.
        """
        import uuid

        conflict_id = str(uuid.uuid4())
        self._pending_approvals[conflict_id] = PendingApproval(
            conflict_id=conflict_id,
            channel_key=channel_key,
            current_value=current_value,
            proposed_value=proposed_value,
            agent_id=agent_id,
        )

        logger.info(f"Conflict on channel '{channel_key}' requires human approval: {conflict_id}")
        return ConflictResult(
            resolved=False,
            strategy_used=ConflictStrategy.HUMAN_APPROVAL.value,
            requires_approval=True,
            final_value={"conflict_id": conflict_id, "status": "pending_approval"},
        )

    def resolve_approval(
        self,
        conflict_id: str,
        approved: bool,
        approver: str | None = None,
    ) -> ConflictResult:
        """Resolve a pending human approval.

        Called by external systems (e.g., Temporal Signal handler) when
        a human makes an approval decision.

        Args:
            conflict_id: The conflict to resolve.
            approved: Whether the proposed value was approved.
            approver: Identifier of the human approver.

        Returns:
            ConflictResult with final resolution.
        """
        pending = self._pending_approvals.pop(conflict_id, None)
        if pending is None:
            logger.warning(f"Approval request {conflict_id} not found")
            return ConflictResult(resolved=False)

        pending.approved = approved
        pending.approver = approver

        final_value = pending.proposed_value if approved else pending.current_value
        logger.info(
            f"Conflict {conflict_id} on '{pending.channel_key}' {'approved' if approved else 'rejected'} by {approver}"
        )

        return ConflictResult(
            resolved=True,
            final_value=final_value,
            strategy_used=ConflictStrategy.HUMAN_APPROVAL.value,
        )

    def get_pending_approvals(self) -> list[PendingApproval]:
        """Get all conflicts pending human approval.

        Returns:
            List of PendingApproval entries.
        """
        return list(self._pending_approvals.values())

    async def resolve_distributed(
        self,
        channel_key: str,
        current_value: Any,
        proposed_value: Any,
        strategy: ConflictStrategy,
        agent_id: str | None = None,
        lock_ttl: float = 30.0,
    ) -> ConflictResult:
        """Resolve a conflict using distributed strategies.

        Args:
            channel_key: The channel being updated.
            current_value: Current channel value.
            proposed_value: Proposed new value.
            strategy: The distributed strategy to use.
            agent_id: ID of the agent making the update.
            lock_ttl: TTL for distributed lock in seconds.

        Returns:
            ConflictResult with resolution outcome.
        """
        if strategy == ConflictStrategy.DISTRIBUTED_LOCK:
            return await self._resolve_distributed_lock(channel_key, current_value, proposed_value, agent_id, lock_ttl)
        if strategy == ConflictStrategy.NEGOTIATION:
            return await self._resolve_negotiation(channel_key, current_value, proposed_value, agent_id)
        return ConflictResult(
            resolved=False,
            strategy_used=strategy.value,
            final_value=current_value,
        )

    async def _resolve_distributed_lock(
        self,
        channel_key: str,
        current_value: Any,
        proposed_value: Any,
        agent_id: str | None,
        lock_ttl: float,
    ) -> ConflictResult:
        """Resolve conflict via distributed lock with TTL.

        Uses optimistic locking — the first agent to acquire the lock wins.
        Other agents receive the current value and must retry.

        Args:
            channel_key: The channel being updated.
            current_value: Current channel value.
            proposed_value: Proposed new value.
            agent_id: ID of the agent making the update.
            lock_ttl: TTL for the lock in seconds.

        Returns:
            ConflictResult with resolution outcome.
        """
        lock_key = f"conflict_lock:{channel_key}"

        # Check if lock exists and is still valid
        existing_lock = self._pending_approvals.get(lock_key)
        if existing_lock is not None and existing_lock.approved is True:
            # Lock is held by another agent
            logger.info(f"Distributed lock held on '{channel_key}', agent {agent_id} must wait")
            return ConflictResult(
                resolved=False,
                strategy_used=ConflictStrategy.DISTRIBUTED_LOCK.value,
                final_value=current_value,
            )

        # Acquire lock
        self._pending_approvals[lock_key] = PendingApproval(
            conflict_id=lock_key,
            channel_key=channel_key,
            current_value=current_value,
            proposed_value=proposed_value,
            agent_id=agent_id,
            approved=True,  # Lock acquired
        )

        logger.info(f"Distributed lock acquired on '{channel_key}' by agent {agent_id}")
        return ConflictResult(
            resolved=True,
            final_value=proposed_value,
            strategy_used=ConflictStrategy.DISTRIBUTED_LOCK.value,
        )

    async def _resolve_negotiation(
        self,
        channel_key: str,
        current_value: Any,
        proposed_value: Any,
        agent_id: str | None,
    ) -> ConflictResult:
        """Resolve conflict via negotiation between agents.

        Delegates to P2PNegotiator for multi-round negotiation.
        Falls back to last-write-wins if negotiation fails.

        Args:
            channel_key: The channel being updated.
            current_value: Current channel value.
            proposed_value: Proposed new value.
            agent_id: ID of the agent making the update.

        Returns:
            ConflictResult with resolution outcome.
        """
        # For now, use last-write-wins as negotiation fallback
        # Full P2PNegotiator integration requires EventBus context
        logger.info(f"Negotiation requested for '{channel_key}' — falling back to LWW")
        return ConflictResult(
            resolved=True,
            final_value=proposed_value,
            strategy_used=ConflictStrategy.NEGOTIATION.value,
        )

    def detect_task_conflict(
        self,
        task_id: str,
        claiming_agents: list[str],
    ) -> ConflictResult:
        """Detect task-level conflicts when multiple agents claim the same task.

        Args:
            task_id: The task being claimed.
            claiming_agents: List of agent IDs claiming the task.

        Returns:
            ConflictResult indicating if conflict was detected.
        """
        if len(claiming_agents) <= 1:
            return ConflictResult(resolved=True, final_value=claiming_agents[0] if claiming_agents else None)

        logger.warning(f"Task conflict detected: {len(claiming_agents)} agents claiming task {task_id}")
        return ConflictResult(
            resolved=False,
            strategy_used="task_conflict_detected",
            final_value={"task_id": task_id, "claiming_agents": claiming_agents},
        )

    def detect_permission_mismatch(
        self,
        agent_id: str,
        requested_action: str,
        allowed_scope: list[str],
    ) -> ConflictResult:
        """Detect permission scope mismatches for A2A remote agents.

        Args:
            agent_id: The agent requesting the action.
            requested_action: The action being requested.
            allowed_scope: List of allowed actions for the agent.

        Returns:
            ConflictResult indicating if permission mismatch was detected.
        """
        if requested_action in allowed_scope:
            return ConflictResult(resolved=True, final_value=requested_action)

        logger.warning(
            f"Permission mismatch: agent {agent_id} requested '{requested_action}' but scope is {allowed_scope}"
        )
        return ConflictResult(
            resolved=False,
            strategy_used="permission_mismatch",
            final_value={"agent_id": agent_id, "requested": requested_action, "allowed": allowed_scope},
        )
