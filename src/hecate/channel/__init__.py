"""Channel domain — inbound / outbound transport to and from external platforms.

This is the transport half of Hecate: where ``runtime/`` owns the
agent execution engine, ``channel/`` owns the seams that connect
the engine to the outside world.

Sub-modules
-----------

- ``adapter`` — ``ChannelBase`` abstract interface; concrete
  implementations ship as plugin packages under ``packages/channels/``
  (``hecate-channel-slack``, ``hecate-channel-feishu`` etc.).
- ``capabilities`` — per-channel capability declarations
  (streaming / markdown / rich_cards / file_upload / max message length).
- ``types`` — ``CanonicalMessage``, ``Attachment``, ``MessageContent``
  IR; IM-specific metadata keys (chat_id, ts, etc.) live here.
- ``resolver`` — entry-point scan for ``hecate.channel_providers`` and
  the ``im_channel_names()`` helper that unions hardcoded prefixes
  with the resolved set.
- ``im/`` — transport-agnostic IM infrastructure (message bus,
  binding service).
- ``a2a/`` — Agent-to-Agent protocol (client + server + signing +
  types). A2A is the second transport alongside IM.
- ``gateway/`` — session router + IM channel registration; the
  entry point that ties IM channels and A2A agents into the agent
  runtime.
- ``management/`` — admin HTTP endpoints that configure and inspect
  channel-domain state (today: alert rules). Filled incrementally as
  Phase R-complete unpacks ``api/management/``.
- ``api/v1/`` — OpenAI-compatible ``/v1/chat/completions`` + IM
  binding + models + agent endpoints; the public API surface for
  the channel domain.

History
-------

This domain directory was created during Phase R-complete; the
sub-modules were relocated from top-level ``src/hecate/api/v1/``,
``src/hecate/a2a/``, ``src/hecate/gateway/``, and one file from
``src/hecate/api/management/alerts.py``. The channel/ directory
itself has carried ``adapter/``, ``capabilities/``, ``types/``,
``resolver/``, ``im/`` since PR5b / the channel plugin-package work;
this PR extends it to its full surface area.

Companion: ``packages/hecate-channel-{slack,feishu}/*`` provide the
concrete channel adapters via the ``hecate.channel_providers`` entry
point group. Future channels (wechat, telegram, teams, dingtalk)
follow the same pattern.
"""

from __future__ import annotations
