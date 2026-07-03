"""Tests for PluginLifecycle protocol."""

from __future__ import annotations

from hecate.plugin.lifecycle import PluginLifecycle


class TestPluginLifecycleProtocol:
    """Tests for PluginLifecycle protocol definition."""

    def test_protocol_is_runtime_checkable(self) -> None:
        """PluginLifecycle can be used with isinstance() at runtime."""

        class MyPlugin:
            def on_load(self) -> None:
                pass

            def on_unload(self) -> None:
                pass

        plugin = MyPlugin()
        assert isinstance(plugin, PluginLifecycle)

    def test_non_lifecycle_plugin_not_instance(self) -> None:
        """Object without lifecycle methods is not an instance."""

        class NoLifecycle:
            pass

        assert not isinstance(NoLifecycle(), PluginLifecycle)

    def test_partial_lifecycle_not_instance(self) -> None:
        """Object with only on_load is not a full PluginLifecycle."""

        class PartialLifecycle:
            def on_load(self) -> None:
                pass

        assert not isinstance(PartialLifecycle(), PluginLifecycle)

    def test_lifecycle_methods_exist(self) -> None:
        """PluginLifecycle protocol defines on_load and on_unload."""
        assert hasattr(PluginLifecycle, "on_load")
        assert hasattr(PluginLifecycle, "on_unload")
