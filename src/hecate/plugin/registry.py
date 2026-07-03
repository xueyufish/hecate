"""PluginRegistry — thread-safe registry for plugin discovery and management.

The PluginRegistry is the central registration point for all SPI extensions.
Plugins register with a :class:`PluginManifest` and are retrievable by type
or name.
"""

from __future__ import annotations

import logging
import threading
from typing import Any

from hecate.plugin.lifecycle import PluginLifecycle
from hecate.plugin.manifest import PluginManifest

logger = logging.getLogger(__name__)


class PluginRegistry:
    """Thread-safe registry for plugin discovery and management.

    Plugins are stored by type and name. Each plugin is associated with
    a :class:`PluginManifest` that describes its metadata.

    Example::

        registry = PluginRegistry()
        registry.register(manifest, my_plugin)
        evaluators = registry.get_by_type("evaluator")
    """

    def __init__(self) -> None:
        self._plugins: dict[str, dict[str, Any]] = {}
        self._manifests: dict[str, dict[str, PluginManifest]] = {}
        self._lock = threading.Lock()

    def register(self, manifest: PluginManifest, plugin: Any) -> None:
        """Register a plugin with its manifest.

        If a plugin with the same type and name already exists, it is
        replaced. If the plugin implements :class:`PluginLifecycle`,
        ``on_load()`` is called after registration.

        Args:
            manifest: The plugin's metadata.
            plugin: The plugin instance.
        """
        with self._lock:
            self._plugins.setdefault(manifest.type, {})[manifest.name] = plugin
            self._manifests.setdefault(manifest.type, {})[manifest.name] = manifest

        # Call lifecycle hook outside lock to avoid deadlocks
        if isinstance(plugin, PluginLifecycle):
            try:
                plugin.on_load()
            except Exception:
                logger.exception(
                    "PluginLifecycle.on_load() failed for %s/%s",
                    manifest.type,
                    manifest.name,
                )

        # Log declared translations (actual loading is done by the plugin
        # via on_load() or by the registration code that has access to the
        # plugin's package directory).
        if manifest.translations:
            logger.info(
                "Plugin %s/%s declares translations: %s",
                manifest.type,
                manifest.name,
                manifest.translations,
            )

    def unregister(self, type: str, name: str) -> None:
        """Unregister a plugin by type and name.

        If the plugin implements :class:`PluginLifecycle`, ``on_unload()``
        is called before removal.

        Args:
            type: The plugin type (e.g., "evaluator").
            name: The plugin name within its type.
        """
        plugin = None
        with self._lock:
            type_plugins = self._plugins.get(type, {})
            plugin = type_plugins.pop(name, None)
            if not type_plugins:
                self._plugins.pop(type, None)
                self._manifests.pop(type, None)
            else:
                self._manifests.get(type, {}).pop(name, None)

        # Call lifecycle hook outside lock
        if plugin is not None and isinstance(plugin, PluginLifecycle):
            try:
                plugin.on_unload()
            except Exception:
                logger.exception(
                    "PluginLifecycle.on_unload() failed for %s/%s",
                    type,
                    name,
                )

    def get_by_type(self, type: str) -> dict[str, Any]:
        """Get all plugins of a given type, keyed by name.

        Args:
            type: The plugin type to retrieve.

        Returns:
            A dictionary mapping plugin names to plugin instances.
        """
        with self._lock:
            return dict(self._plugins.get(type, {}))

    def get_by_name(self, type: str, name: str) -> Any | None:
        """Get a specific plugin by type and name.

        Args:
            type: The plugin type.
            name: The plugin name.

        Returns:
            The plugin instance, or None if not found.
        """
        with self._lock:
            return self._plugins.get(type, {}).get(name)

    def get_manifest(self, type: str, name: str) -> PluginManifest | None:
        """Get the manifest for a specific plugin.

        Args:
            type: The plugin type.
            name: The plugin name.

        Returns:
            The plugin manifest, or None if not found.
        """
        with self._lock:
            return self._manifests.get(type, {}).get(name)

    def list_all(self) -> dict[str, dict[str, Any]]:
        """Get all registered plugins grouped by type.

        Returns:
            A dictionary mapping plugin types to dictionaries of
            plugin name -> plugin instance.
        """
        with self._lock:
            return {t: dict(ps) for t, ps in self._plugins.items()}
