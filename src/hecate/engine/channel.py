"""Channel-based state management for graph execution.

Channels are named state slots that form the shared memory of a executing graph.
Each channel has type-specific write semantics (overwrite, append, or reduce) that
determine how worker outputs are merged into the global state. All reads return deep
copies to guarantee isolation between concurrent workers within a superstep.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from hecate.engine.eviction import EvictionPolicy, NoEviction
from hecate.engine.types import ChannelDef, ChannelType


class Channel:
    """A named state slot with type-specific write semantics and deep-copy isolation.

    A Channel holds a single mutable value whose update behavior depends on its
    ChannelType:

    - LAST_VALUE: the new value replaces the old one entirely.
    - TOPIC / PERSISTENT_TOPIC: values are appended to a list (lists extend, scalars append).
    - ACCUMULATOR: the new value is combined with the old one via a reduce function
      (currently only ``"add"`` is supported; unsupported reduce functions overwrite).

    All reads (``read`` and ``snapshot``) return deep copies so that workers never
    observe mutations caused by other workers in the same superstep.
    """

    def __init__(self, name: str, defn: ChannelDef) -> None:
        self.name = name
        self.defn = defn
        self._value: Any = deepcopy(defn.default) if defn.default is not None else self._initial_value()

    def _initial_value(self) -> Any:
        if self.defn.type == ChannelType.TOPIC or self.defn.type == ChannelType.PERSISTENT_TOPIC:
            return []
        if self.defn.type == ChannelType.ACCUMULATOR:
            return deepcopy(self.defn.initial) if self.defn.initial is not None else 0
        return None

    def write(self, value: Any) -> None:
        """Write a value using the channel's type-specific semantics.

        Behavior by ChannelType:
        - LAST_VALUE: replaces ``_value`` with ``value``.
        - TOPIC / PERSISTENT_TOPIC: if ``value`` is a list, extends the internal
          list; otherwise appends the scalar.
        - ACCUMULATOR: if ``reduce_fn`` is ``"add"``, adds ``value`` to the current
          accumulator; otherwise overwrites with ``value``.
        """
        if self.defn.type == ChannelType.LAST_VALUE:
            self._value = value
        elif self.defn.type in (ChannelType.TOPIC, ChannelType.PERSISTENT_TOPIC):
            if isinstance(value, list):
                self._value.extend(value)
            else:
                self._value.append(value)
        elif self.defn.type == ChannelType.ACCUMULATOR:
            if self.defn.reduce_fn == "add":
                self._value = (self._value or 0) + value
            else:
                self._value = value

    def read(self) -> Any:
        """Return a deep copy of the current value."""
        return deepcopy(self._value)

    def snapshot(self) -> Any:
        """Return a deep copy snapshot for checkpoint persistence."""
        return self.read()


class ChannelManager:
    """Registry that manages all named channels for a graph execution.

    The ChannelManager is the central state container used by the Pregel runtime.
    It registers channels from a CompiledGraph definition and provides read/write
    access by name. It also supports snapshot/restore for checkpoint persistence.
    """

    def __init__(self, eviction_policy: EvictionPolicy | None = None) -> None:
        self._channels: dict[str, Channel] = {}
        self._eviction_policy: EvictionPolicy = eviction_policy or NoEviction()

    def register(self, name: str, defn: ChannelDef) -> None:
        """Register a new channel with the given definition."""
        self._channels[name] = Channel(name, defn)

    def write(self, name: str, value: Any) -> None:
        """Write a value to the named channel.

        Silently skips unregistered channels. This intentional no-op design allows
        workers to produce output for channels that may not exist in every graph
        configuration without requiring conditional logic at call sites.
        """
        if name not in self._channels:
            return
        channel = self._channels[name]
        channel.write(value)
        if channel.defn.type in (
            ChannelType.TOPIC,
            ChannelType.PERSISTENT_TOPIC,
        ) and self._eviction_policy.should_evict(name, len(channel._value), {}):
            channel._value = self._eviction_policy.select_victim(channel._value)

    def read(self, name: str) -> Any:
        """Read a value from the named channel.

        Raises:
            KeyError: if the channel is not registered.
        """
        if name not in self._channels:
            raise KeyError(f"Channel '{name}' not registered")
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
