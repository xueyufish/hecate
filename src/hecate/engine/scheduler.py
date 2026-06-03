"""Pluggable scheduling strategies for graph node execution.

Provides the abstract contract (SchedulerStrategy) and a default implementation:
- ``FIFOScheduler`` — returns nodes in input order (first-in, first-out)

SchedulerStrategy controls the order in which ready nodes are dispatched
each superstep. Implementations may provide priority-based, fair-sharing,
or other scheduling algorithms.
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class SchedulerStrategy(ABC):
    """Abstract interface for node scheduling during graph execution.

    A SchedulerStrategy determines the order in which ready nodes are
    dispatched each superstep. The engine calls ``select_next`` to get
    the ordered list, then dispatches workers in that order.
    """

    @abstractmethod
    def select_next(self, nodes: list[str], context: dict) -> list[str]:
        """Return nodes in the order they should be executed.

        Args:
            nodes: List of ready node IDs for this superstep.
            context: Execution context (superstep number, channel snapshot, etc.).

        Returns:
            Ordered list of node IDs.
        """
        ...

    @abstractmethod
    def set_weights(self, weights: dict[str, float]) -> None:
        """Set execution weights/priorities for nodes.

        Args:
            weights: Dict mapping node_id to weight (higher = higher priority).
        """
        ...


class FIFOScheduler(SchedulerStrategy):
    """First-in, first-out scheduler — returns nodes in input order.

    This is the default scheduler that preserves the original sequential
    execution behavior. It ignores weights and always returns nodes
    in the order they were provided.
    """

    def select_next(self, nodes: list[str], context: dict) -> list[str]:
        """Return nodes in their original order.

        Args:
            nodes: List of ready node IDs.
            context: Execution context (unused by FIFO).

        Returns:
            The same list, unchanged.
        """
        return list(nodes)

    def set_weights(self, weights: dict[str, float]) -> None:
        """No-op — FIFO ignores weights.

        Args:
            weights: Ignored.
        """
