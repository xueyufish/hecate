"""Channel — external platform adapter framework.

This subpackage defines the abstract interfaces and data types for
channel adapters that connect external platforms (Feishu, Slack,
Telegram, Email, etc.) to the Hecate agent runtime via the Gateway.
"""

from __future__ import annotations

from hecate.channel.adapter import ChannelABC
from hecate.channel.capabilities import ChannelCapabilities
from hecate.channel.types import Attachment, CanonicalMessage, MessageContent

__all__ = [
    "Attachment",
    "CanonicalMessage",
    "ChannelABC",
    "ChannelCapabilities",
    "MessageContent",
]
