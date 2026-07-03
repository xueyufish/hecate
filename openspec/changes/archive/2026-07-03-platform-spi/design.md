## Context

Hecate is an enterprise-grade agent platform with 15 extension points. The Plugin SPI Core (5.5a) was recently completed, establishing the pattern for SPI extension points: EvaluatorABC → BuiltinEvaluator → PluginRegistry.

This change extends that pattern to four new SPI areas: external platform adapters (ChannelABC), authentication providers (AuthProviderABC), internationalization (i18n SPI), and merges the existing notification system into the Channel model.

The current codebase has:
- REST API as the sole agent interaction channel (`api/v1/chat.py`, 454 lines)
- Monolithic auth flow in `core/deps_workspace.py` (JWT → API Key → Env Key)
- Hardcoded notification dispatch in `services/notification_dispatcher.py` (switch/case on ChannelType)
- Zero i18n infrastructure

Industry analysis (OpenClaw, AgentScope, Salesforce AgentForce, Amazon Bedrock AgentCore, ch4p, CAR) confirms the Gateway + Channel Adapter pattern as the standard approach.

## Goals / Non-Goals

**Goals:**
- Define ChannelABC as the abstract interface for external platform adapters (not REST/WS/CLI transport)
- Introduce a Gateway layer for session routing and message normalization
- Define AuthProviderABC for pluggable authentication (same pattern as EvaluatorABC)
- Define i18n SPI with complete scope: locale detection, message catalog, `t()` function, plugin translations, management API, runtime switching
- Merge NotificationDispatcher into Channel model (unified `type="channel"`)
- Zero breaking changes to existing code — all changes are additive

**Non-Goals:**
- Implementing specific platform channels (Feishu, Slack, Discord) — that's Sprint 6+
- Migrating existing REST API to Gateway — gradual migration, not big-bang
- SSO/LDAP/OAuth2 AuthProvider implementations — only the ABC and built-in JWT/APIKey
- Performance optimization — correctness first, optimization later

## Decisions

### D1: ChannelABC is an external platform adapter, not a transport abstraction

**Decision:** ChannelABC abstracts "how to talk to a specific platform" (Feishu Bot API, Slack Bolt SDK, Telegram grammY), not "how to transport messages" (HTTP, WebSocket, stdin/stdout).

**Rationale:** Industry consensus (OpenClaw's ChannelPlugin, AgentScope's Channel Adapter, ch4p's IChannel, CAR's ChannelAdapter) all define channels as platform-specific adapters with a canonical message format. REST/WS/CLI are Gateway transport layers — they handle the wire protocol, not the platform semantics.

**Alternative considered:** Define ChannelABC as REST/WS/CLI abstraction (original roadmap). Rejected because REST, WebSocket, and CLI are architecturally too different (synchronous vs async, stateless vs stateful, process-based vs persistent) to share a meaningful ABC.

### D2: Gateway is a new layer, not a replacement

**Decision:** Gateway sits between channels and WorkflowExecutionService. It receives CanonicalMessage from channels, resolves session context, and delegates to the existing service layer.

```
Channel.receive(raw) → CanonicalMessage → Gateway.route() → WorkflowExecutionService → PregelRuntime
```

**Rationale:** Salesforce's "build once, deploy everywhere" pattern. The agent logic (WorkflowExecutionService, PregelRuntime) is completely channel-agnostic. Gateway adds session routing without touching business logic.

**Alternative considered:** Channels call WorkflowExecutionService directly (no Gateway). Rejected because it duplicates session routing logic across every channel.

### D3: CanonicalMessage is the universal message format

**Decision:** Define a frozen dataclass `CanonicalMessage` with fields: `id`, `channel_id`, `user_id`, `session_id`, `content` (text + attachments), `metadata` (platform-specific passthrough), `timestamp`.

**Rationale:** Every industry implementation (OpenClaw, AgentScope, ch4p, CAR) normalizes platform-specific messages into a canonical format at the edge. The agent brain only sees CanonicalMessage.

### D4: ChannelCapabilities uses a declarative model

**Decision:** Each Channel declares its capabilities via a frozen dataclass:

```python
@dataclasses.dataclass(frozen=True)
class ChannelCapabilities:
    streaming: bool = False
    interactive_buttons: bool = False
    file_upload: bool = False
    markdown: bool = False
    rich_cards: bool = False
    max_message_length: int | None = None
```

**Rationale:** OpenClaw's ISP approach — capabilities are declared, not branched. The Gateway checks capabilities before attempting operations. If `streaming=False`, the Gateway sends a buffered response instead.

### D5: NotifierABC merged into ChannelABC (unified type="channel")

**Decision:** Notification dispatchers (Email, Feishu card, Slack Block Kit, Webhook) become Channel adapters. PluginRegistry uses `type="channel"` for all of them.

**Rationale:** A notification is a form of outbound communication to a platform — structurally identical to a Channel's `respond()` method. Separating them adds complexity without benefit.

**Alternative considered:** Keep NotifierABC separate with `type="notifier"`. Rejected because the distinction is artificial — both "send a message to a platform."

### D6: AuthProviderABC follows EvaluatorABC pattern exactly

**Decision:**
```python
class AuthProviderABC(ABC):
    @property
    @abstractmethod
    def name(self) -> str: ...
    
    @property
    @abstractmethod
    def description(self) -> str: ...
    
    @abstractmethod
    async def authenticate(self, token: str, db: AsyncSession) -> AuthContext | None: ...
```

Built-in: `JWTAuthProvider`, `APIKeyAuthProvider`. The existing `get_auth_context()` iterates registered providers, first non-None result wins.

**Rationale:** Pattern proven with EvaluatorABC. The authenticate() returns `None` on failure (not an exception), allowing the next provider to be tried.

### D7: i18n SPI uses JSON/YAML message catalogs

**Decision:** Translation files use JSON format (primary) with YAML support. File structure: `locales/{lang}/{namespace}.json`. The `t()` function supports nested keys, pluralization, and parameter interpolation.

**Rationale:** JSON is universally supported, easy to validate, and human-readable. YAML as secondary for teams that prefer it. gettext is too complex for a Python-first platform.

**Alternative considered:** gettext (.po/.mo). Rejected because it requires compilation steps and has poor Python ecosystem tooling compared to JSON.

### D8: i18n management API for translation file upload

**Decision:** REST endpoints under `/api/i18n/`:
- `POST /api/i18n/translations` — upload a translation file (JSON/YAML)
- `GET /api/i18n/translations/{locale}` — download current translations
- `GET /api/i18n/locales` — list available locales
- `PUT /api/i18n/translations/{locale}/{namespace}` — update specific namespace

**Rationale:** Enterprise requirement — teams need to manage translations via API, not just files.

### D9: Plugin translation registration via PluginManifest

**Decision:** Plugins declare their translation namespaces in PluginManifest. When a plugin is registered, its translations are automatically loaded.

```python
@dataclasses.dataclass(frozen=True)
class PluginManifest:
    # ... existing fields ...
    translations: tuple[str, ...] = ()  # e.g., ("plugin-name:common", "plugin-name:errors")
```

**Rationale:** Plugins need their own translations. Declaring them in the manifest ensures they're loaded when the plugin registers, without separate configuration.

## Risks / Trade-offs

**[Risk] Gateway becomes a bottleneck** → Mitigation: Gateway is stateless and horizontally scalable. Session routing is a simple lookup, not a heavy computation.

**[Risk] ChannelCapabilities grows unbounded** → Mitigation: Capabilities are declared per-channel, not global. New capabilities are added to the dataclass with `False` defaults — existing channels don't break.

**[Risk] AuthProvider iteration order matters** → Mitigation: Providers are registered with explicit ordering. The first non-None result wins, which is the standard pattern (same as how `get_auth_context()` already works with JWT → APIKey → EnvKey).

**[Risk] i18n translation file conflicts** → Mitigation: Namespaced keys (e.g., `plugin-name:key`). Later registrations override earlier ones for the same namespace. Management API shows which plugin owns each key.

**[Risk] Migrating existing REST API to Gateway** → Mitigation: Gradual migration. The existing `api/v1/chat.py` continues to work directly with WorkflowExecutionService. A `RESTChannelAdapter` is added that wraps the same logic. Gateway routes through it. No big-bang migration.

## Open Questions

1. Should Gateway be a separate process (like ch4p) or in-process (like AgentScope)?
   - Current thinking: in-process for simplicity, with the option to extract later.

2. Should CanonicalMessage include conversation history or just the current message?
   - Current thinking: just the current message. History is managed by the session/context system, not the channel.
