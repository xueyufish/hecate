"""Plugin type registry — maps type strings to ABC classes."""

from __future__ import annotations

from typing import Any

from hecate.channel.adapter import ChannelBase
from hecate.core.plugin.spi.evaluator import EvaluatorBase

# New ABCs
from hecate.core.plugin.types.extension import ExtensionPluginBase
from hecate.core.plugin.types.model import ModelPluginBase
from hecate.core.plugin.types.tool import ToolPluginBase
from hecate.core.plugin.types.trigger import TriggerPluginBase

# Existing ABCs
from hecate.enterprise.auth.provider import AuthProvider
from hecate.enterprise.vault.provider import SecretProvider

__all__ = [
    "AuthProvider",
    "ChannelBase",
    "EvaluatorBase",
    "ExtensionPluginBase",
    "ModelPluginBase",
    "SecretProvider",
    "ToolPluginBase",
    "TriggerPluginBase",
    "PLUGIN_TYPE_REGISTRY",
]

PLUGIN_TYPE_REGISTRY: dict[str, type[Any]] = {
    "tool": ToolPluginBase,
    "extension": ExtensionPluginBase,
    "trigger": TriggerPluginBase,
    "model": ModelPluginBase,
    "channel": ChannelBase,
    "evaluator": EvaluatorBase,
    "auth_provider": AuthProvider,
    "secret_provider": SecretProvider,
}
