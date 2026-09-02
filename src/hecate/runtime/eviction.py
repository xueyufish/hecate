"""Pluggable eviction policies for channel state management.

Provides the abstract contract (EvictionPolicy) and implementations:
- ``NoEviction`` — never evict (default, preserves unbounded growth)
- ``SizeBasedEviction`` — evict oldest items when channel exceeds max size

EvictionPolicy controls when and how items are removed from TOPIC channels
that can grow unboundedly during long-running sessions.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class EvictionPolicy(ABC):
    """Abstract interface for channel eviction decisions.

    An EvictionPolicy determines when a channel should evict items and
    which items to keep. The engine calls ``should_evict`` after each
    write to a TOPIC channel, and ``select_victim`` if eviction is needed.
    """

    @abstractmethod
    def should_evict(self, channel_name: str, current_size: int, context: dict) -> bool:
        """Determine if eviction should occur.

        Args:
            channel_name: Name of the channel being checked.
            current_size: Current number of items in the channel.
            context: Execution context (superstep, session info, etc.).

        Returns:
            True if eviction should occur, False otherwise.
        """
        ...

    @abstractmethod
    def select_victim(self, items: list[Any]) -> list[Any]:
        """Select which items to keep (evict the rest).

        Args:
            items: Current items in the channel.

        Returns:
            List of items to keep (items not in this list will be evicted).
        """
        ...


class NoEviction(EvictionPolicy):
    """Never evict — preserves unbounded growth (default behavior).

    This policy maintains the original ChannelManager behavior where
    TOPIC channels grow without limit.
    """

    def should_evict(self, channel_name: str, current_size: int, context: dict) -> bool:
        """Never evict.

        Args:
            channel_name: Ignored.
            current_size: Ignored.
            context: Ignored.

        Returns:
            Always False.
        """
        return False

    def select_victim(self, items: list[Any]) -> list[Any]:
        """Keep all items.

        Args:
            items: Current items.

        Returns:
            All items unchanged.
        """
        return list(items)


class SizeBasedEviction(EvictionPolicy):
    """Evict oldest items when channel size exceeds max_size.

    Keeps the most recent max_size items, evicting the oldest.
    """

    def __init__(self, max_size: int) -> None:
        """Initialize with maximum channel size.

        Args:
            max_size: Maximum number of items to keep in a channel.
        """
        self._max_size = max_size

    def should_evict(self, channel_name: str, current_size: int, context: dict) -> bool:
        """Check if channel size exceeds maximum.

        Args:
            channel_name: Ignored.
            current_size: Current number of items.
            context: Ignored.

        Returns:
            True if current_size >= max_size.
        """
        return current_size >= self._max_size

    def select_victim(self, items: list[Any]) -> list[Any]:
        """Keep the newest max_size items.

        Args:
            items: Current items (ordered oldest to newest).

        Returns:
            The last max_size items (newest).
        """
        if len(items) <= self._max_size:
            return list(items)
        return items[-self._max_size :]
