"""Tests for PluginLifecycle protocol."""

from __future__ import annotations

from typing import Any

from hecate.plugin.lifecycle import PluginLifecycle


class TestPluginLifecycleProtocol:
    """Tests for PluginLifecycle protocol definition."""

    def test_full_lifecycle_is_instance(self) -> None:
        """Plugin implementing all hooks satisfies the protocol."""

        class FullPlugin:
            def on_load(self) -> None:
                pass

            def on_unload(self) -> None:
                pass

            def on_enable(self) -> None:
                pass

            def on_disable(self) -> None:
                pass

            def on_config_change(self, new_config: dict[str, Any]) -> None:
                pass

        assert isinstance(FullPlugin(), PluginLifecycle)

    def test_non_lifecycle_plugin_not_instance(self) -> None:
        """Object without lifecycle methods is not an instance."""

        class NoLifecycle:
            pass

        assert not isinstance(NoLifecycle(), PluginLifecycle)

    def test_partial_lifecycle_not_instance(self) -> None:
        """Object with only on_load does not satisfy full protocol."""

        class PartialLifecycle:
            def on_load(self) -> None:
                pass

        assert not isinstance(PartialLifecycle(), PluginLifecycle)

    def test_lifecycle_methods_exist(self) -> None:
        """PluginLifecycle protocol defines all expected hooks."""
        assert hasattr(PluginLifecycle, "on_load")
        assert hasattr(PluginLifecycle, "on_unload")
        assert hasattr(PluginLifecycle, "on_enable")
        assert hasattr(PluginLifecycle, "on_disable")
        assert hasattr(PluginLifecycle, "on_config_change")
