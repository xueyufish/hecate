"""SPI (Service Provider Interface) extension point definitions.

This subpackage contains the abstract interfaces for SPI extension points.
Each SPI type defines a contract that built-in and third-party plugins
must implement.
"""

from __future__ import annotations

from hecate.auth.provider import AuthProvider
from hecate.channel.adapter import ChannelBase
from hecate.plugin.spi.evaluator import EvaluatorBase
from hecate.vault.provider import SecretProvider

__all__ = [
    "AuthProvider",
    "ChannelBase",
    "EvaluatorBase",
    "SecretProvider",
]
