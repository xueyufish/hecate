## 1. Channel Adapter Core (channel-adapter spec)

- [x] 1.1 Create `src/hecate/channel/__init__.py` with public exports (ChannelABC, CanonicalMessage, ChannelCapabilities)
- [x] 1.2 Create `src/hecate/channel/types.py` with CanonicalMessage frozen dataclass (id, channel_id, user_id, session_id, content, metadata, timestamp) and MessageContent dataclass (text, attachments)
- [x] 1.3 Create `src/hecate/channel/capabilities.py` with ChannelCapabilities frozen dataclass (streaming, interactive_buttons, file_upload, markdown, rich_cards, max_message_length)
- [x] 1.4 Create `src/hecate/channel/adapter.py` with ChannelABC abstract base class (name, description, capabilities properties; receive, respond, stream abstract methods)

## 2. Gateway Layer (channel-adapter spec)

- [x] 2.1 Create `src/hecate/gateway/__init__.py` with public exports (Gateway)
- [x] 2.2 Create `src/hecate/gateway/session.py` with SessionRouter (session_id → channel_id/user_id mapping, create/resume logic)
- [x] 2.3 Create `src/hecate/gateway/gateway.py` with Gateway class (accepts CanonicalMessage from channels, resolves session via SessionRouter, delegates to WorkflowExecutionService)

## 3. Channel Plugin Registration (channel-adapter spec)

- [x] 3.1 Update `src/hecate/plugin/spi/__init__.py` to export ChannelABC
- [x] 3.2 Create `register_channels(registry)` function in `src/hecate/gateway/registration.py` that registers built-in channels (RESTChannelAdapter placeholder)

## 4. NotificationDispatcher Refactor (channel-adapter spec)

- [x] 4.1 Create `src/hecate/channel/notification.py` with NotificationChannelAdapter base class that wraps existing render functions as Channel respond() implementations
- [x] 4.2 Refactor `src/hecate/services/notification_dispatcher.py` to use NotificationChannelAdapter instead of switch/case dispatch

## 5. AuthProviderABC (auth-provider spec)

- [x] 5.1 Create `src/hecate/auth/__init__.py` with public exports (AuthProviderABC, JWTAuthProvider, APIKeyAuthProvider)
- [x] 5.2 Create `src/hecate/auth/provider.py` with AuthProviderABC abstract base class (name, description properties; authenticate abstract method)
- [x] 5.3 Create `src/hecate/auth/jwt_provider.py` with JWTAuthProvider that wraps existing `decode_access_token()` and returns AuthContext
- [x] 5.4 Create `src/hecate/auth/api_key_provider.py` with APIKeyAuthProvider that wraps existing `_resolve_api_key()` logic and returns AuthContext

## 6. Auth Provider Integration (auth-provider spec)

- [x] 6.1 Create `src/hecate/auth/resolver.py` with `resolve_auth_context(credentials, db)` function that iterates registered auth providers
- [x] 6.2 Update `src/hecate/core/deps_workspace.py` to delegate `get_auth_context()` to `resolve_auth_context()` while preserving existing behavior
- [x] 6.3 Create `register_auth_providers(registry)` function that registers JWTAuthProvider and APIKeyAuthProvider

## 7. i18n Core (i18n-spi spec)

- [x] 7.1 Create `src/hecate/i18n/__init__.py` with public exports (LocaleResolver, MessageCatalog, t)
- [x] 7.2 Create `src/hecate/i18n/locale_resolver.py` with LocaleResolver (priority: explicit param → Accept-Language header → user preference → workspace default → system default "en")
- [x] 7.3 Create `src/hecate/i18n/catalog.py` with MessageCatalog that loads from `locales/{locale}/{namespace}.json` or `.yaml`, supports nested key lookup and parameter interpolation
- [x] 7.4 Create `src/hecate/i18n/translate.py` with `t(key, locale=None, **params)` function that uses LocaleResolver and MessageCatalog

## 8. i18n Data Model (i18n-spi spec)

- [x] 8.1 Add `preferred_locale` (optional str) field to `src/hecate/models/user.py` UserModel
- [x] 8.2 Add `default_locale` (optional str, default "en") field to `src/hecate/models/workspace.py` WorkspaceModel (if exists)
- [x] 8.3 Create Alembic migration for new locale fields

## 9. i18n Plugin Translation Registration (i18n-spi spec)

- [x] 9.1 Update `src/hecate/plugin/manifest.py` to add `translations: tuple[str, ...] = ()` field to PluginManifest
- [x] 9.2 Update `src/hecate/plugin/registry.py` to auto-load plugin translations on registration

## 10. i18n Management API (i18n-spi spec)

- [x] 10.1 Create `src/hecate/api/management/i18n.py` with REST endpoints: POST /api/i18n/translations, GET /api/i18n/translations/{locale}, GET /api/i18n/locales, PUT /api/i18n/translations/{locale}/{namespace}
- [x] 10.2 Register i18n router in `src/hecate/main.py`

## 11. Plugin SPI __init__.py Update

- [x] 11.1 Update `src/hecate/plugin/spi/__init__.py` to export all new ABCs (ChannelABC, AuthProviderABC)

## 12. Tests

- [x] 12.1 Create `tests/test_channel/test_adapter.py` — test ChannelABC interface, CanonicalMessage immutability, ChannelCapabilities defaults
- [x] 12.2 Create `tests/test_channel/test_gateway.py` — test Gateway session routing, message normalization
- [x] 12.3 Create `tests/test_auth/test_provider.py` — test AuthProviderABC interface, JWTAuthProvider, APIKeyAuthProvider
- [x] 12.4 Create `tests/test_auth/test_resolver.py` — test provider iteration, fallback behavior
- [x] 12.5 Create `tests/test_i18n/test_locale_resolver.py` — test locale detection priority chain
- [x] 12.6 Create `tests/test_i18n/test_catalog.py` — test JSON/YAML loading, nested keys, parameter interpolation, fallback
- [x] 12.7 Create `tests/test_i18n/test_translate.py` — test t() function end-to-end
- [x] 12.8 Run full test suite: `ruff check src/hecate/ tests/ && ruff format --check src/ tests/ && mypy src/ && python -m pytest tests/ -q`
