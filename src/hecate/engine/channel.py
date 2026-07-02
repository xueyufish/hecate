"""Channel-based state management for graph execution.

Channels are named state slots that form the shared memory of a executing graph.
Each channel has type-specific write semantics (overwrite, append, or reduce) that
determine how worker outputs are merged into the global state. All reads return deep
copies to guarantee isolation between concurrent workers within a superstep.

Channel behavior is defined by ``ChannelBehavior`` implementations registered in
the module-level ``ChannelTypeRegistry``. The registry maps type name strings to
behavior objects, allowing custom channel types to be registered at runtime.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from copy import deepcopy
from typing import Any

from hecate.engine.errors import ChannelNotFoundError
from hecate.engine.eviction import EvictionPolicy, NoEviction
from hecate.engine.types import ChannelAccess, ChannelDef, ChannelType

logger = logging.getLogger(__name__)


# ============================================================
# ChannelBehavior ABC
# ============================================================


class ChannelBehavior(ABC):
    """Abstract base class defining the behavioral contract for channel types.

    Each channel type must implement 4 concerns:
    - ``initial_value``: what value a new channel starts with.
    - ``write``: how a new value is merged into the current value.
    - ``is_evictable``: whether the eviction policy applies to this type.
    - ``resolve_conflict``: how concurrent writes are merged.
    """

    @abstractmethod
    def initial_value(self, defn: ChannelDef) -> Any:
        """Return the initial value for a channel with this definition."""

    @abstractmethod
    def write(self, current: Any, value: Any, defn: ChannelDef) -> Any:
        """Return the new value after applying write semantics."""

    @abstractmethod
    def is_evictable(self) -> bool:
        """Return True if the eviction policy applies to this channel type."""

    @abstractmethod
    def resolve_conflict(self, current: Any, proposed: Any) -> Any:
        """Return the merged value for concurrent writes."""


class LastValueBehavior(ChannelBehavior):
    """Overwrite semantics: new value replaces old."""

    def initial_value(self, defn: ChannelDef) -> Any:
        return None

    def write(self, current: Any, value: Any, defn: ChannelDef) -> Any:
        return value

    def is_evictable(self) -> bool:
        return False

    def resolve_conflict(self, current: Any, proposed: Any) -> Any:
        return proposed


class TopicBehavior(ChannelBehavior):
    """Append semantics: new values are appended to a list."""

    def initial_value(self, defn: ChannelDef) -> Any:
        return []

    def write(self, current: Any, value: Any, defn: ChannelDef) -> Any:
        if isinstance(value, list):
            return current + value
        return [*current, value]

    def is_evictable(self) -> bool:
        return True

    def resolve_conflict(self, current: Any, proposed: Any) -> Any:
        if not isinstance(current, list):
            current = [current] if current is not None else []
        if not isinstance(proposed, list):
            proposed = [proposed] if proposed is not None else []
        seen: set[str] = set()
        merged: list[Any] = []
        for item in current + proposed:
            key = str(item)
            if key not in seen:
                seen.add(key)
                merged.append(item)
        return merged


class AccumulatorBehavior(ChannelBehavior):
    """Reduce semantics: values are combined via a reduce function."""

    def initial_value(self, defn: ChannelDef) -> Any:
        return deepcopy(defn.initial) if defn.initial is not None else 0

    def write(self, current: Any, value: Any, defn: ChannelDef) -> Any:
        if defn.reduce_fn == "add":
            return (current or 0) + value
        return value

    def is_evictable(self) -> bool:
        return False

    def resolve_conflict(self, current: Any, proposed: Any) -> Any:
        try:
            return (current or 0) + (proposed or 0)
        except (TypeError, ValueError):
            return proposed


# ============================================================
# ChannelTypeRegistry
# ============================================================

_REGISTRY: dict[str, ChannelBehavior] = {}


def register(name: str, behavior: ChannelBehavior) -> None:
    """Register a channel behavior for a type name.

    Args:
        name: The channel type string (e.g. "last_value", "topic").
        behavior: The behavior implementation for this type.
    """
    _REGISTRY[name] = behavior
    logger.debug(f"Registered channel type '{name}' → {behavior.__class__.__name__}")


def get(name: str) -> ChannelBehavior:
    """Get the registered behavior for a channel type name.

    Args:
        name: The channel type string.

    Returns:
        The registered ChannelBehavior.

    Raises:
        KeyError: If no behavior is registered for this type.
    """
    if name not in _REGISTRY:
        raise KeyError(f"Channel type '{name}' not registered. Available types: {', '.join(sorted(_REGISTRY))}")
    return _REGISTRY[name]


def list_types() -> list[str]:
    """Return all registered channel type names."""
    return sorted(_REGISTRY.keys())


# Pre-register built-in types
register(ChannelType.LAST_VALUE, LastValueBehavior())
register(ChannelType.TOPIC, TopicBehavior())
register(ChannelType.ACCUMULATOR, AccumulatorBehavior())
register(ChannelType.PERSISTENT_TOPIC, TopicBehavior())


# ============================================================
# Channel
# ============================================================


class Channel:
    """A named state slot with behavior-delegated write semantics and deep-copy isolation.

    All type-specific logic is delegated to the registered ``ChannelBehavior``.
    Reads (``read`` and ``snapshot``) return deep copies so that workers never
    observe mutations caused by other workers in the same superstep.
    """

    def __init__(self, name: str, defn: ChannelDef) -> None:
        self.name = name
        self.defn = defn
        self._value: Any = deepcopy(defn.default) if defn.default is not None else self._initial_value()

    def _initial_value(self) -> Any:
        """Delegate initial value to the registered behavior."""
        return get(self.defn.type).initial_value(self.defn)

    def write(self, value: Any) -> None:
        """Write a value using the registered behavior's semantics."""
        behavior = get(self.defn.type)
        self._value = behavior.write(self._value, value, self.defn)

    def read(self) -> Any:
        """Return a deep copy of the current value."""
        return deepcopy(self._value)

    def snapshot(self) -> Any:
        """Return a deep copy snapshot for checkpoint persistence."""
        return self.read()


# ============================================================
# ChannelManager
# ============================================================


class ChannelManager:
    """Registry that manages all named channels for a graph execution.

    The ChannelManager is the central state container used by the Pregel runtime.
    It registers channels from a CompiledGraph definition and provides read/write
    access by name. It also supports snapshot/restore for checkpoint persistence.
    """

    def __init__(
        self,
        eviction_policy: EvictionPolicy | None = None,
        channel_access: dict[str, ChannelAccess] | None = None,
    ) -> None:
        self._channels: dict[str, Channel] = {}
        self._eviction_policy: EvictionPolicy = eviction_policy or NoEviction()
        self._channel_access: dict[str, ChannelAccess] = channel_access or {}

    def register(self, name: str, defn: ChannelDef) -> None:
        """Register a new channel with the given definition."""
        self._channels[name] = Channel(name, defn)

    def write(self, name: str, value: Any, node_id: str | None = None) -> None:
        """Write a value to the named channel.

        Silently skips unregistered channels. This intentional no-op design allows
        workers to produce output for channels that may not exist in every graph
        configuration without requiring conditional logic at call sites.

        Args:
            name: Channel name.
            value: Value to write.
            node_id: Optional node ID for channel access validation.
        """
        if node_id is not None and node_id in self._channel_access:
            access = self._channel_access[node_id]
            if name not in access.writable:
                logger.warning("Node '%s' writes to channel '%s' without declaring it as writable", node_id, name)
        if name not in self._channels:
            return
        channel = self._channels[name]
        channel.write(value)
        behavior = get(channel.defn.type)
        if behavior.is_evictable() and self._eviction_policy.should_evict(name, len(channel._value), {}):
            channel._value = self._eviction_policy.select_victim(channel._value)

    def read(self, name: str, node_id: str | None = None) -> Any:
        """Read a value from the named channel.

        Args:
            name: Channel name.
            node_id: Optional node ID for channel access validation.

        Raises:
            KeyError: if the channel is not registered.
        """
        if node_id is not None and node_id in self._channel_access:
            access = self._channel_access[node_id]
            if name not in access.readable:
                logger.warning("Node '%s' reads from channel '%s' without declaring it as readable", node_id, name)
        if name not in self._channels:
            raise ChannelNotFoundError(name)
        return self._channels[name].read()

    def snapshot(self) -> dict[str, Any]:
        """Return a snapshot of all channel values for checkpoint persistence."""
        return {name: ch.snapshot() for name, ch in self._channels.items()}

    def restore(self, state: dict[str, Any]) -> None:
        """Restore channel values from a previously saved snapshot.

        This directly sets each channel's ``_value`` field, bypassing the type-
        specific write semantics. This is intentional: restoration must reproduce
        the exact state that was captured at checkpoint time (e.g., a TOPIC
        channel should receive the full list, not append it element-by-element).
        """
        for name, value in state.items():
            if name in self._channels:
                self._channels[name]._value = deepcopy(value)
