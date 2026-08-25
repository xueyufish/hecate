"""Plugin type registry — maps type strings to ABC classes."""

from __future__ import annotations

from typing import Any

# Existing ABCs
from hecate.auth.provider import AuthProviderBase
from hecate.channel.adapter import ChannelBase
from hecate.plugin.spi.evaluator import EvaluatorBase

# New ABCs
from hecate.plugin.types.extension import ExtensionPluginBase
from hecate.plugin.types.model import ModelPluginBase
from hecate.plugin.types.tool import ToolPluginBase
from hecate.plugin.types.trigger import TriggerPluginBase
from hecate.vault.provider import SecretProviderBase

__all__ = [
    "AuthProviderBase",
    "ChannelBase",
    "EvaluatorBase",
    "ExtensionPluginBase",
    "ModelPluginBase",
    "SecretProviderBase",
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
    "auth_provider": AuthProviderBase,
    "secret_provider": SecretProviderBase,
}
