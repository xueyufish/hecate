"""hecate.plugin SDK module — single import path for plugin development.

Import all 8 type ABCs and helper utilities from here::

    from hecate.plugin.sdk import ToolPluginBase, PluginContext
"""

from __future__ import annotations

from typing import Any

from hecate.auth.provider import AuthProvider
from hecate.channel.adapter import ChannelBase
from hecate.plugin.permission import PermissionChecker
from hecate.plugin.spi.evaluator import EvaluatorBase
from hecate.plugin.types.extension import ExtensionPluginBase
from hecate.plugin.types.model import ModelPluginBase
from hecate.plugin.types.tool import ToolPluginBase
from hecate.plugin.types.trigger import TriggerPluginBase
from hecate.vault.provider import SecretProvider

__all__ = [
    "AuthProvider",
    "ChannelBase",
    "EvaluatorBase",
    "ExtensionPluginBase",
    "ModelPluginBase",
    "PluginContext",
    "SecretProvider",
    "ToolPluginBase",
    "TriggerPluginBase",
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
