"""Plugin type registry — maps type strings to ABC classes."""

from __future__ import annotations

from typing import Any

# Existing ABCs
from hecate.auth.provider import AuthProviderABC
from hecate.channel.adapter import ChannelABC
from hecate.plugin.spi.evaluator import EvaluatorABC

# New ABCs
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
    "SecretProviderABC",
    "ToolPluginABC",
    "TriggerPluginABC",
    "PLUGIN_TYPE_REGISTRY",
]

PLUGIN_TYPE_REGISTRY: dict[str, type[Any]] = {
    "tool": ToolPluginABC,
    "extension": ExtensionPluginABC,
    "trigger": TriggerPluginABC,
    "model": ModelPluginABC,
    "channel": ChannelABC,
    "evaluator": EvaluatorABC,
    "auth_provider": AuthProviderABC,
    "secret_provider": SecretProviderABC,
}
