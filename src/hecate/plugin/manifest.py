"""PluginManifest — immutable metadata describing a plugin.

A PluginManifest captures the essential information needed to register,
discover, and manage a plugin within the PluginRegistry.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PluginManifest:
    """Immutable metadata describing a plugin.

    Every plugin registered with :class:`PluginRegistry` must provide a
    manifest that identifies the plugin, its version, and its requirements.

    Attributes:
        type: Plugin type identifier (e.g., "evaluator", "channel",
            "auth_provider", "notifier", "tool").
        name: Unique plugin name within its type.
        version: Semantic version string (e.g., "1.0.0").
        api_version: API version this plugin targets.
        min_platform_version: Minimum platform version required.
        description: Human-readable description of the plugin.
        permissions: Required permissions (e.g., ["network:https"]).
    """

    type: str
    name: str
    version: str
    api_version: str = ""
    min_platform_version: str = ""
    description: str = ""
    permissions: tuple[str, ...] = ()
    translations: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        """Convert list fields to tuples for immutability."""
        if isinstance(self.permissions, list):
            object.__setattr__(self, "permissions", tuple(self.permissions))
        if isinstance(self.translations, list):
            object.__setattr__(self, "translations", tuple(self.translations))
