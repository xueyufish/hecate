"""Plugin SPI Core — centralized plugin registration and lifecycle management.

This module provides the foundation for all SPI (Service Provider Interface)
extensions in Hecate. It defines:

- :class:`PluginManifest` — immutable metadata describing a plugin
- :class:`PluginRegistry` — thread-safe registry for plugin discovery
- :class:`PluginLifecycle` — optional protocol for plugin initialization hooks

Usage::

    from hecate.core.plugin import PluginManifest, PluginRegistry

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

from hecate.core.plugin.lifecycle import PluginLifecycle
from hecate.core.plugin.manifest import PluginManifest
from hecate.core.plugin.registry import PluginRegistry
from hecate.core.plugin.sdk import PluginContext
from hecate.core.plugin.types.extension import ExtensionPluginBase
from hecate.core.plugin.types.model import ModelPluginBase
from hecate.core.plugin.types.tool import ToolPluginBase
from hecate.core.plugin.types.trigger import TriggerPluginBase

__all__ = [
    "ExtensionPluginBase",
    "ModelPluginBase",
    "PluginContext",
    "PluginLifecycle",
    "PluginManifest",
    "PluginRegistry",
    "ToolPluginBase",
    "TriggerPluginBase",
]
