"""Gateway — session routing and message normalization layer.

The Gateway sits between channel adapters and the agent runtime
(WorkflowExecutionService). It receives CanonicalMessage from channels,
resolves session context, and delegates to the service layer.

Two session routers coexist:

- ``SessionRouter`` — in-memory router for non-IM channels (used by
  gateway.py for HTTP / a2a transports where persistence is not
  required).
- ``IMSessionRouter`` — IM-channel-aware router with a deterministic
  SHA-256 → conversation-UUID mapping. A user bound to multiple IM
  channels (Feishu + Slack) shares one conversation thread across
  channels (design.md D4).
"""

from __future__ import annotations

from hecate.channel.gateway.gateway import Gateway
from hecate.channel.gateway.im_session_router import IMSessionRouter
from hecate.channel.gateway.session import SessionRouter

__all__ = [
    "Gateway",
    "IMSessionRouter",
    "SessionRouter",
]
