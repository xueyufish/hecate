"""Tests for PluginRegistry."""

from __future__ import annotations

from unittest.mock import MagicMock

from hecate.plugin.lifecycle import PluginLifecycle
from hecate.plugin.manifest import PluginManifest
from hecate.plugin.registry import PluginRegistry


def _make_manifest(type: str = "evaluator", name: str = "test") -> PluginManifest:
    """Helper to create a minimal PluginManifest."""
    return PluginManifest(type=type, name=name, version="1.0.0")


class TestPluginRegistry:
    """Tests for PluginRegistry registration and discovery."""

    def test_register_and_get_by_name(self) -> None:
        """Registered plugin can be retrieved by type and name."""
        registry = PluginRegistry()
        manifest = _make_manifest()
        plugin = MagicMock()

        registry.register(manifest, plugin)

        assert registry.get_by_name("evaluator", "test") is plugin

    def test_register_replaces_duplicate(self) -> None:
        """Registering with same type+name replaces the old plugin."""
        registry = PluginRegistry()
        manifest = _make_manifest()
        plugin1 = MagicMock()
        plugin2 = MagicMock()

        registry.register(manifest, plugin1)
        registry.register(manifest, plugin2)

        assert registry.get_by_name("evaluator", "test") is plugin2

    def test_unregister(self) -> None:
        """Unregistered plugin is no longer retrievable."""
        registry = PluginRegistry()
        manifest = _make_manifest()
        plugin = MagicMock()

        registry.register(manifest, plugin)
        registry.unregister("evaluator", "test")

        assert registry.get_by_name("evaluator", "test") is None

    def test_unregister_nonexistent(self) -> None:
        """Unregistering a nonexistent plugin does not raise."""
        registry = PluginRegistry()
        registry.unregister("evaluator", "nonexistent")

    def test_get_by_type(self) -> None:
        """get_by_type returns all plugins of that type."""
        registry = PluginRegistry()
        p1 = MagicMock()
        p2 = MagicMock()

        registry.register(_make_manifest(name="a"), p1)
        registry.register(_make_manifest(name="b"), p2)

        result = registry.get_by_type("evaluator")
        assert result == {"a": p1, "b": p2}

    def test_get_by_type_empty(self) -> None:
        """get_by_type returns empty dict for unknown type."""
        registry = PluginRegistry()
        assert registry.get_by_type("unknown") == {}

    def test_get_by_name_not_found(self) -> None:
        """get_by_name returns None for unknown plugin."""
        registry = PluginRegistry()
        assert registry.get_by_name("evaluator", "unknown") is None

    def test_get_manifest(self) -> None:
        """get_manifest returns the manifest for a registered plugin."""
        registry = PluginRegistry()
        manifest = _make_manifest()
        plugin = MagicMock()

        registry.register(manifest, plugin)

        assert registry.get_manifest("evaluator", "test") is manifest

    def test_get_manifest_not_found(self) -> None:
        """get_manifest returns None for unknown plugin."""
        registry = PluginRegistry()
        assert registry.get_manifest("evaluator", "unknown") is None

    def test_list_all(self) -> None:
        """list_all returns all plugins grouped by type."""
        registry = PluginRegistry()
        p1 = MagicMock()
        p2 = MagicMock()
        p3 = MagicMock()

        registry.register(_make_manifest(type="evaluator", name="a"), p1)
        registry.register(_make_manifest(type="evaluator", name="b"), p2)
        registry.register(_make_manifest(type="channel", name="rest"), p3)

        result = registry.list_all()
        assert result == {
            "evaluator": {"a": p1, "b": p2},
            "channel": {"rest": p3},
        }

    def test_list_all_empty(self) -> None:
        """list_all returns empty dict when no plugins registered."""
        registry = PluginRegistry()
        assert registry.list_all() == {}


class TestPluginRegistryLifecycle:
    """Tests for PluginRegistry lifecycle integration."""

    def test_on_load_called_on_register(self) -> None:
        """PluginLifecycle.on_load() is called when plugin is registered."""
        registry = PluginRegistry()
        manifest = _make_manifest()
        plugin = MagicMock(spec=PluginLifecycle)

        registry.register(manifest, plugin)

        plugin.on_load.assert_called_once()

    def test_on_unload_called_on_unregister(self) -> None:
        """PluginLifecycle.on_unload() is called when plugin is unregistered."""
        registry = PluginRegistry()
        manifest = _make_manifest()
        plugin = MagicMock(spec=PluginLifecycle)

        registry.register(manifest, plugin)
        registry.unregister("evaluator", "test")

        plugin.on_unload.assert_called_once()

    def test_lifecycle_not_called_for_non_lifecycle_plugin(self) -> None:
        """No lifecycle hooks called for plugins without PluginLifecycle."""
        registry = PluginRegistry()
        manifest = _make_manifest()
        plugin = MagicMock()  # No spec=PluginLifecycle

        registry.register(manifest, plugin)
        registry.unregister("evaluator", "test")

        # No exception raised, no lifecycle methods called
        assert not hasattr(plugin, "on_load") or not plugin.on_load.called

    def test_lifecycle_exception_does_not_propagate(self) -> None:
        """Lifecycle hook exceptions are logged but do not propagate."""
        registry = PluginRegistry()
        manifest = _make_manifest()
        plugin = MagicMock(spec=PluginLifecycle)
        plugin.on_load.side_effect = RuntimeError("load failed")

        # Should not raise
        registry.register(manifest, plugin)

        # Plugin is still registered despite lifecycle failure
        assert registry.get_by_name("evaluator", "test") is plugin
