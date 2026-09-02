"""hecate-channel-slack — Slack IM channel adapter for Hecate.

Implements the ``ChannelBase`` contract from ``hecate.channel.adapter``
and registers itself via the ``hecate.channel_providers`` entry-point
group; see :mod:`hecate_channel_slack.provider`.
"""

from __future__ import annotations

from .channel import SlackChannel, create_slack_channel
from .provider import provider

__all__ = ["SlackChannel", "create_slack_channel", "provider"]
