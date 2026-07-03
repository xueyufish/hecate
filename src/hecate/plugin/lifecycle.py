"""PluginLifecycle — optional protocol for plugin initialization hooks.

Plugins MAY implement this protocol to receive lifecycle callbacks
when they are registered or unregistered from the PluginRegistry.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class PluginLifecycle(Protocol):
    """Optional protocol for plugin lifecycle hooks.

    Plugins that implement this protocol receive callbacks when they
    are registered (``on_load``) or unregistered (``on_unload``) from
    the :class:`PluginRegistry`.

    Example::

        class MyPlugin:
            def on_load(self) -> None:
                print("Plugin loaded!")

            def on_unload(self) -> None:
                print("Plugin unloaded!")
    """

    def on_load(self) -> None:
        """Called when the plugin is registered."""
        ...

    def on_unload(self) -> None:
        """Called when the plugin is unregistered."""
        ...
