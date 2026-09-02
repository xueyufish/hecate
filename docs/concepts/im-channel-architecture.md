# IM Channel Architecture

Hecate's IM channel layer lets external instant-messaging platforms (Feishu,
Slack) deliver user messages into the agent runtime and route Agent
responses back. This document describes the architecture, the data flow,
and the mandatory Bound Identity model.

## Overview

```
  Feishu / Slack user
        │
        ▼
  IM Platform (Lark, Slack App)
        │  Webhook POST
        ▼
  ┌────────────────────────────────────────────┐
  │  POST /v1/channels/{name}/webhook          │
  │  (FastAPI router, src/hecate/api/v1/channels.py) │
  └────────────┬───────────────────────────────┘
               │ Signature verification (SDK)
               ▼
  ┌────────────────────────────────────────────┐
  │  ChannelBase adapter                         │
  │  (FeishuChannel / SlackChannel)              │
  │  receive(raw) → CanonicalMessage             │
  └────────────┬───────────────────────────────┘
               │
               ▼
  ┌────────────────────────────────────────────┐
  │  IMMessageBus                                │
  │  Enqueue + return 200 OK within 200ms       │
  └────────────┬───────────────────────────────┘
               │
               ▼ (asyncio task, background)
  ┌────────────────────────────────────────────┐
  │  IMBindingService.resolve_identity()        │
  │  → Hecate user (or issue binding token)     │
  └────────────┬───────────────────────────────┘
               │
               ▼
  ┌────────────────────────────────────────────┐
  │  IMSessionRouter.resolve_or_create()        │
  │  SHA-256(workspace, user)                   │
  │  → deterministic conversation UUID          │
  └────────────┬───────────────────────────────┘
               │
               ▼
  ┌────────────────────────────────────────────┐
  │  WorkflowExecutionService.execute()          │
  │  (existing PregelRuntime path)               │
  └────────────┬───────────────────────────────┘
               │
               ▼
  ┌────────────────────────────────────────────┐
  │  ChannelBase.respond() or .stream()           │
  │  Route reply back to Feishu / Slack          │
  └────────────────────────────────────────────┘
```

The first three boxes (webhook → adapter → bus) run on the
**request thread** and must finish within the IM platform's webhook
window — Feishu and Slack both expect a 200 OK in under ~3 seconds.
The ``IMMessageBus`` is the boundary that hands off to a background
task: the adapter enqueues the normalized :class:`CanonicalMessage` and
returns immediately, while a worker picks it up and walks through
binding resolution, session routing, and the existing workflow
executor. The last box — ``ChannelBase.respond()`` — runs on the
**worker thread** and posts the Agent's reply back to the IM platform.
The horizontal arrows are message handoffs; the vertical arrows are
time. Anything outside the boxes is platform glue (signature
verification, asyncio task scheduling) and does not affect the data
shape.

## Key Components

### ChannelBase and the Plugin Registry

`hecate/channel/adapter.py` defines `ChannelBase`, an abstract base class
with three methods: `receive`, `respond`, and `stream`. Since PR5b the
concrete implementations live in the channel plugin packages under
`packages/channels/`:

- `hecate-channel-feishu` — wraps `lark_oapi.channel.FeishuChannel`
- `hecate-channel-slack` — wraps `slack_bolt.App`

Both are registered with the existing `PluginRegistry` under
`type="channel"` at startup. The webhook endpoint resolves the adapter by
name.

### ChannelProvider contract & entry-points route (PR5a)

`ChannelBase` (in `hecate/channel/adapter.py`) is the single shared
interface every IM channel implements. PR5a formalised it with four
non-abstract default hooks (subclasses stay source-compatible):

- `on_load()` / `on_unload()` — lifecycle (default no-op).
- `health_check() -> str` — coarse ops signal (default `"ok"`).
- `verify_webhook(headers, raw_body) -> tuple[int, dict]` — signed/encrypted
  webhook verification (default `(200, {})`, meaning "not
  platform-verified, continue to `receive`"). The webhook route calls it
  before JSON decoding — signatures cover the raw body, and encrypted
  payloads are not JSON-decodable until decrypted. `hecate-channel-feishu`
  delegates to `lark_oapi`'s `handle_webhook_request`;
  `hecate-channel-slack` implements the signing-secrets `v0` scheme
  directly (HMAC-SHA256 over `v0:<timestamp>:<body>` with a five-minute
  replay window) — webhook POSTs terminate at Hecate's FastAPI endpoint
  and never flow through Bolt's `RequestVerification` middleware.

Channel discovery uses the `hecate.channel_providers` entry-point group,
mirroring the `hecate.memory_providers` / `hecate.llm_providers` pattern.
The adapters live in the channel plugin packages
`packages/channels/hecate-channel-{feishu,slack}/` (PR5b), which register
under that group; the historical in-core entries were retired when the
packages were extracted. `Settings.CHANNEL_PROVIDERS` (default
`("feishu", "slack")`) is a **tuple** because channels are inherently
multi-instance — Feishu and Slack run side by side, unlike memory/llm
which are single-select.

`register_im_channels` calls `resolve_channel_providers()` first, then
falls back to the historical env-gated soft-import branches (so an
environment where entry-point metadata is unavailable still boots
correctly). `im_channel_names()` unions the historical hardcoded prefixes
with the resolver's output, so a newly installed channel package routes
correctly without editing `gateway.py`.

### CanonicalMessage and ChannelCapabilities

`hecate/channel/types.py` defines `CanonicalMessage`, the universal message
format the Gateway and Agent runtime consume. IM-specific data (chat
identifier, IM user identifier, message timestamps) lives in
`CanonicalMessage.metadata` to keep the IR minimal.

`hecate/channel/capabilities.py` declares `ChannelCapabilities` so that
downstream code (LLMWorker, response rendering) can adapt to the channel.
For example, `SlackChannel.capabilities.markdown = False` because Slack
uses `mrkdwn`, not standard Markdown.

### IMMessageBus — async decoupling

`hecate/channel/im/message_bus.py` provides an in-process asyncio queue
that decouples webhook ACK (must return within ~3 seconds) from Agent
execution (can take many seconds). Webhook handlers `enqueue` and return
200 OK immediately; background workers consume the queue, invoke
`WorkflowExecutionService.execute`, and route the result back through the
adapter.

### IMBindingService — mandatory Bound Identity

`hecate/channel/im/binding.py` enforces the design decision that every
IM user must be bound to a Hecate user before any conversation begins.
The flow:

1. An unbound IM user sends a message.
2. The Gateway's `resolve_identity` lookup returns `None`.
3. The system issues a one-time, 10-minute token (stored as SHA-256 hash).
4. The bot replies to the IM user with a binding URL.
5. The user clicks the URL, logs into the Web UI, and confirms.
6. `confirm_token` validates and creates an `IMIdentityBindingModel`
   row in the same transaction.

See `specs/im-channel-feishu-slack/spec.md` for the full requirements.

### IMSessionRouter — deterministic conversation IDs

`hecate/services/im_session_router.py` derives a deterministic UUID for
the conversation from `SHA-256(workspace_id | user_id)`. The same
Hecate user bound to multiple IM channels (Feishu + Slack) therefore
shares one conversation thread across both channels — a requirement
specified in `specs/data-models/spec.md`.

### Webhook endpoint

`src/hecate/api/v1/channels.py` exposes
`POST /v1/channels/{name}/webhook`. Signature verification is delegated
to the underlying SDK (`lark_oapi.handle_webhook_request` or
`slack_bolt.RequestVerification`). URL-verification challenges from the
platforms are echoed back without enqueuing.

## Data Model

Six objects carry the IM-channel feature. The first three are shared
with the existing ``/v1/chat/completions`` API path; the next two are
IM-only; the last is a marker field that lets you tell where a row
came from.

### Shared objects — used by both IM and API

These rows look identical whether the originating channel is Feishu,
Slack, or the OpenAI-compatible HTTP API. IM traffic is identified by
the ``source_channel`` field described further down; the rows
themselves are not duplicated.

**``ConversationModel``** — one row per thread. Holds the
``agent_id`` and ``workspace_id`` that scope the conversation, plus
quality and topic metadata. An IM conversation is structurally the same
as an API conversation; it lives in the same table.

**``MessageModel``** — one row per turn (system / user / assistant /
tool). The ``role`` and ``content`` fields are unchanged; the
``metadata_`` JSON holds IM-platform-specific keys (``chat_id``,
``message_id``, ``thread_ts``) when ``source_channel`` is not
``NULL``.

**``SessionModel``** — execution state for one conversation
(checkpoint id, current node, status, agent id). The same table backs
``/v1/chat/completions`` sessions.

### IM-only objects

These two tables exist solely because the Bound Identity model needs
to map an IM-platform user to a Hecate user before any conversation
can start. The ``/v1/chat/completions`` path never reads them.

**``IMIdentityBindingModel``** — joins an IM-platform user to a
Hecate user within a workspace. Composite key:
``(workspace_id, channel_type, im_app_id, im_user_id, unbound_at)``.
This is what makes a Slack user who works in workspace *A* and *B* map
to two different Hecate users — workspace scoping is built into the
identity, not added later.

**``IMBindingTokenModel``** — one-time, 10-minute tokens issued by
:class:`IMBindingService.issue_token` and consumed by
:meth:`IMBindingService.confirm_token`. Only the SHA-256 hash is
persisted; the plaintext lives solely in the binding URL that goes
back to the IM user.

### How a row's origin is recorded

The single new field ``source_channel`` (nullable ``String(32)`` on
``conversations``, ``messages``, and ``sessions``) records which
channel produced each row:

- ``"feishu"`` — the row was written by the Feishu IM adapter.
- ``"slack"`` — the row was written by the Slack IM adapter.
- ``NULL`` — the row was written by the OpenAI-compatible
  ``/v1/chat/completions`` path or any other non-IM entry point.

A second field, ``conversations.im_chat_id`` (``String(128)``,
nullable), caches the IM chat identifier for reply routing so the
adapter can target the same chat without re-resolving it.

### Entity relationships

```
Workspace ──┬── Conversation ──┬── Message
            │                  └── Session
            │
            └── User ─── IMIdentityBinding ─── IMBindingToken (transient)
```

IM traffic does not fork into a parallel hierarchy — every IM message
becomes one ``ConversationModel`` row plus its child ``MessageModel``
and ``SessionModel`` rows, exactly like an API message. The two
IM-only tables are consulted once at message-arrival time to resolve
the IM user to a Hecate user; after that the conversation flows
through the same engine and persistence layer as any other Hecate
chat.

## Operational Notes

- **Public URL**: Feishu and Slack webhooks both require a publicly
  reachable URL. In development, expose Hecate through ngrok or a
  similar tunnel.
- **Socket Mode** (Slack only): set `HECATE_IM_SLACK_APP_TOKEN` to a
  Socket-Mode token to avoid the public-URL requirement.
- **Tenant isolation**: every binding lookup is scoped by
  `workspace_id`; cross-workspace leakage is structurally impossible.
- **Secret storage**: IM App credentials are read from environment
  variables. Production deployments should source these from the
  existing `SecretProvider` (HashiCorp Vault, AWS Secrets Manager,
  etc.).