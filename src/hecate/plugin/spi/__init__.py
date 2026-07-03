"""SPI (Service Provider Interface) extension point definitions.

This subpackage contains the abstract interfaces for SPI extension points.
Each SPI type defines a contract that built-in and third-party plugins
must implement.
"""

from __future__ import annotations

from hecate.auth.provider import AuthProviderABC
from hecate.channel.adapter import ChannelABC
from hecate.plugin.spi.evaluator import EvaluatorABC

__all__ = [
    "AuthProviderABC",
    "ChannelABC",
    "EvaluatorABC",
]
