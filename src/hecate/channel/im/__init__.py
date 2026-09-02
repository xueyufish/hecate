"""IM channel infrastructure — message bus and identity binding.

The platform adapters themselves (Feishu, Slack) live in the channel
plugin packages (``packages/channels/hecate-channel-*/``) since PR5b and
are discovered via the ``hecate.channel_providers`` entry-point group.
This package keeps the transport-agnostic infrastructure:

- :class:`IMMessageBus` — async decoupling between webhook ACK and Agent execution
- :class:`IMBindingService` — mandatory IM identity binding workflow
"""

from __future__ import annotations
