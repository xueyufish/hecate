"""hecate-channel-feishu — Feishu (Lark) IM channel adapter for Hecate.

Implements the ``ChannelBase`` contract from ``hecate.channel.adapter``
and registers itself via the ``hecate.channel_providers`` entry-point
group; see :mod:`hecate_channel_feishu.provider`.
"""

from __future__ import annotations

from .channel import FeishuChannel, create_feishu_channel
from .provider import provider

__all__ = ["FeishuChannel", "create_feishu_channel", "provider"]
