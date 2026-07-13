"""hecate.plugin SDK module — single import path for plugin development.

Import all 8 type ABCs and helper utilities from here::

    from hecate.plugin.sdk import ToolPluginABC, PluginContext
"""

from __future__ import annotations

from typing import Any

from hecate.auth.provider import AuthProviderABC
from hecate.channel.adapter import ChannelABC
from hecate.plugin.permission import PermissionChecker
from hecate.plugin.spi.evaluator import EvaluatorABC
from hecate.plugin.types.extension import ExtensionPluginABC
from hecate.plugin.types.model import ModelPluginABC
from hecate.plugin.types.tool import ToolPluginABC
from hecate.plugin.types.trigger import TriggerPluginABC
from hecate.vault.provider import SecretProviderABC

__all__ = [
    "AuthProviderABC",
    "ChannelABC",
    "EvaluatorABC",
    "ExtensionPluginABC",
    "ModelPluginABC",
    "PluginContext",
    "SecretProviderABC",
    "ToolPluginABC",
    "TriggerPluginABC",
]


class PluginContext:
    """Runtime context injected into plugin lifecycle methods.

    Provides access to configuration values and permission checking.
    """

    def __init__(
        self,
        config: dict[str, Any] | None = None,
        permissions: tuple[str, ...] = (),
    ) -> None:
        self._config = config or {}
        self._checker = PermissionChecker(permissions)

    @property
    def config(self) -> dict[str, Any]:
        return self._config

    def check_permission(self, permission: str) -> bool:
        return self._checker.check(permission)
