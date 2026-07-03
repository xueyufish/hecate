"""Plugin SPI Core — centralized plugin registration and lifecycle management.

This module provides the foundation for all SPI (Service Provider Interface)
extensions in Hecate. It defines:

- :class:`PluginManifest` — immutable metadata describing a plugin
- :class:`PluginRegistry` — thread-safe registry for plugin discovery
- :class:`PluginLifecycle` — optional protocol for plugin initialization hooks

Usage::

    from hecate.plugin import PluginManifest, PluginRegistry

    manifest = PluginManifest(
        type="evaluator",
        name="faithfulness",
        version="1.0.0",
        api_version="1.0",
        min_platform_version="0.5.0",
        description="Detects ungrounded claims",
        permissions=[],
    )
    registry = PluginRegistry()
    registry.register(manifest, faithfulness_evaluator)
"""

from __future__ import annotations

from hecate.plugin.lifecycle import PluginLifecycle
from hecate.plugin.manifest import PluginManifest
from hecate.plugin.registry import PluginRegistry

__all__ = [
    "PluginLifecycle",
    "PluginManifest",
    "PluginRegistry",
]
