## Why

Hecate's agent interaction is currently hardcoded to REST API (`api/v1/chat.py`). Adding a new platform (Feishu, Slack, Discord, Telegram) requires modifying core code. The notification system (`NotificationDispatcher`) uses switch/case on channel types, making it equally rigid. Authentication is a monolithic `get_auth_context()` function that can't be extended without editing it. And there is zero i18n infrastructure.

This change introduces the Platform SPI layer — a plugin-based architecture for external platform adapters, authentication providers, and internationalization — following the Salesforce "build once, deploy everywhere" pattern and the EvaluatorABC precedent.

## What Changes

- **ChannelABC** (new SPI): Abstract interface for external platform adapters. Each Channel adapts a specific platform (Feishu, Slack, Telegram, Email, Webhook) to a canonical message format. The existing REST API, CLI, and NotificationDispatcher become Channel implementations.
- **Gateway** (new layer): Sits between channels and the agent runtime. Handles session routing, message normalization, and delegates to WorkflowExecutionService. REST API routes are preserved — Gateway is additive, not a replacement.
- **AuthProviderABC** (new SPI): Abstract interface for authentication providers. Built-in: JWTAuthProvider, APIKeyAuthProvider. The existing `get_auth_context()` iterates registered providers. Future: SAML, LDAP, OAuth2.
- **i18n SPI** (new): Locale detection, message catalog loading, `t()` translation function, plugin translation registration, management API for translation files, runtime language switching.
- **NotifierABC merged into ChannelABC**: Notification dispatchers (Email, Feishu card, Slack Block Kit) become Channel adapters. PluginRegistry uses unified `type="channel"`.

## Capabilities

### New Capabilities

- `channel-adapter`: ChannelABC interface for external platform adapters, Gateway layer for session routing and message normalization, CanonicalMessage format, ChannelCapabilities declaration
- `auth-provider`: AuthProviderABC interface for pluggable authentication, built-in JWT and APIKey providers, provider registration and iteration in auth flow
- `i18n-spi`: Locale detection (request header / user preference / workspace setting), MessageCatalog loading (JSON/YAML), `t()` translation function, plugin translation registration, management API for translation file upload, runtime language switching

### Modified Capabilities

- `builtin-evaluators`: No requirement changes — only implementation reference for pattern consistency

## Impact

**Zero breaking changes.** All existing code continues to work unchanged:

| Component | Impact |
|-----------|--------|
| PregelRuntime | None — engine internals untouched |
| WorkflowExecutionService | None — business logic layer untouched |
| Auth system | None — `get_auth_context()` preserved, providers added alongside |
| Management APIs | None — these are management endpoints, not agent interaction channels |
| REST API (`api/v1/chat.py`) | Gradual migration — becomes a Channel Adapter, but API contract preserved |
| CLI (`cli/client.py`) | Gradual migration — becomes a Channel Adapter |
| `notification_dispatcher.py` | Gradual migration — becomes outbound Channel Adapter pattern |

**New code locations:**
- `src/hecate/gateway/` — Gateway, CanonicalMessage, session routing
- `src/hecate/channel/` — ChannelAdapter ABC, built-in adapters
- `src/hecate/auth/` — AuthProviderABC, built-in providers
- `src/hecate/i18n/` — LocaleResolver, MessageCatalog, `t()` function

**Dependencies:** Plugin SPI Core (5.5a) — completed and merged.
