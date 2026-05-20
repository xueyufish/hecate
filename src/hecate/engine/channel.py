from __future__ import annotations

from copy import deepcopy
from typing import Any

from hecate.engine.types import ChannelDef, ChannelType


class Channel:
    """A named state slot with type-specific write semantics."""

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
        """Write a value using the channel's type-specific semantics."""
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
    """Registry that manages all named channels for a graph execution."""

    def __init__(self) -> None:
        self._channels: dict[str, Channel] = {}

    def register(self, name: str, defn: ChannelDef) -> None:
        """Register a new channel with the given definition."""
        self._channels[name] = Channel(name, defn)

    def write(self, name: str, value: Any) -> None:
        """Write a value to the named channel. Silently skips unregistered channels."""
        if name not in self._channels:
            return
        self._channels[name].write(value)

    def read(self, name: str) -> Any:
        """Read a value from the named channel."""
        if name not in self._channels:
            raise KeyError(f"Channel '{name}' not registered")
        return self._channels[name].read()

    def snapshot(self) -> dict[str, Any]:
        """Return a snapshot of all channel values for checkpoint persistence."""
        return {name: ch.snapshot() for name, ch in self._channels.items()}

    def restore(self, state: dict[str, Any]) -> None:
        """Restore channel values from a previously saved snapshot."""
        for name, value in state.items():
            if name in self._channels:
                self._channels[name]._value = deepcopy(value)
