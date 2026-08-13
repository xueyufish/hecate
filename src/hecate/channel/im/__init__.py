"""IM channel adapters — Feishu (Lark) and Slack.

Thin layer over official IM SDKs (`lark_oapi.channel`, `slack_bolt`) that
normalizes platform-specific messages into Hecate's `CanonicalMessage` and
routes Agent responses back to the originating platform.

Public API (populated as tasks 3-8 land):

- :class:`FeishuChannel` — Feishu/Lark inbound + outbound adapter
- :class:`SlackChannel` — Slack inbound + outbound adapter
- :class:`IMMessageBus` — async decoupling between webhook ACK and Agent execution
- :class:`IMBindingService` — mandatory IM identity binding workflow
"""

from __future__ import annotations
