"""PluginLifecycle — optional protocol for plugin initialization hooks.

Plugins MAY implement this protocol to receive lifecycle callbacks
when they are registered, unregistered, enabled, disabled, or
reconfigured within the PluginRegistry.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class PluginLifecycle(Protocol):
    """Optional protocol for plugin lifecycle hooks.

    Plugins that implement this protocol receive callbacks when they
    are registered (``on_load``), unregistered (``on_unload``),
    enabled (``on_enable``), disabled (``on_disable``), or when their
    configuration changes (``on_config_change``) in the
    :class:`PluginRegistry`.

    All methods are optional — a plugin need only implement the hooks
    it cares about. The loader detects implemented hooks via
    ``hasattr()``.

    Example::

        class MyPlugin:
            def on_load(self) -> None:
                print("Plugin loaded!")

            def on_enable(self) -> None:
                print("Plugin enabled!")

            def on_unload(self) -> None:
                print("Plugin unloaded!")
    """

    def on_load(self) -> None:
        """Called when the plugin is registered."""
        ...

    def on_unload(self) -> None:
        """Called when the plugin is unregistered."""
        ...

    def on_enable(self) -> None:
        """Called when the plugin transitions to enabled state."""
        ...

    def on_disable(self) -> None:
        """Called when the plugin transitions to disabled state."""
        ...

    def on_config_change(self, new_config: dict[str, Any]) -> None:
        """Called when the plugin configuration is updated.

        Args:
            new_config: The new configuration dictionary after the update.
        """
        ...
