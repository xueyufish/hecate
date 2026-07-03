## ADDED Requirements

### Requirement: Locale detection from multiple sources
The system SHALL implement a `LocaleResolver` that detects locale from, in priority order: (1) explicit `locale` parameter in the request, (2) `Accept-Language` HTTP header, (3) user's preferred locale setting, (4) workspace default locale, (5) system default locale (`en`).

#### Scenario: Locale from request parameter
- **WHEN** a request includes `locale="zh-CN"`
- **THEN** the resolved locale SHALL be `"zh-CN"`, ignoring header and user preferences

#### Scenario: Locale from Accept-Language header
- **WHEN** no explicit locale is provided and the header is `Accept-Language: ja, en;q=0.9`
- **THEN** the resolved locale SHALL be `"ja"`

#### Scenario: Locale from user preference
- **WHEN** no explicit locale and no header, but user's preferred locale is `"de"`
- **THEN** the resolved locale SHALL be `"de"`

#### Scenario: Locale from workspace default
- **WHEN** no explicit locale, no header, no user preference, and workspace default is `"fr"`
- **THEN** the resolved locale SHALL be `"fr"`

#### Scenario: Fallback to system default
- **WHEN** no locale can be resolved from any source
- **THEN** the resolved locale SHALL be `"en"`

### Requirement: MessageCatalog loads translations from JSON/YAML files
The system SHALL implement a `MessageCatalog` that loads translation files from `locales/{locale}/{namespace}.json` or `locales/{locale}/{namespace}.yaml`. Supported formats: JSON (primary), YAML (secondary).

#### Scenario: Load JSON translations
- **WHEN** `MessageCatalog.load("zh-CN", "common")` is called and `locales/zh-CN/common.json` exists
- **THEN** all keys from the file SHALL be available for lookup

#### Scenario: Load YAML translations
- **WHEN** `MessageCatalog.load("ja", "errors")` is called and `locales/ja/errors.yaml` exists
- **THEN** all keys from the file SHALL be available for lookup

#### Scenario: Missing translation file
- **WHEN** `MessageCatalog.load("ko", "common")` is called and no Korean file exists
- **THEN** the catalog SHALL return None for any key lookup in that locale/namespace

### Requirement: t() function for translation lookup
The system SHALL provide a `t(key, locale=None, **params)` function that looks up a translation key. The function SHALL support: dotted keys for nested lookups (`t("errors.not_found")`), parameter interpolation (`t("greeting", name="Alice")` → `"Hello, Alice"`), and fallback to the key itself when no translation is found.

#### Scenario: Simple key lookup
- **WHEN** `t("errors.not_found", locale="zh-CN")` is called and `zh-CN` has `{"errors": {"not_found": "未找到"}}`
- **THEN** it SHALL return `"未找到"`

#### Scenario: Parameter interpolation
- **WHEN** `t("greeting", locale="en", name="Alice")` is called and `en` has `{"greeting": "Hello, {name}"}`
- **THEN** it SHALL return `"Hello, Alice"`

#### Scenario: Missing key fallback
- **WHEN** `t("nonexistent.key", locale="en")` is called
- **THEN** it SHALL return `"nonexistent.key"` (the key itself)

#### Scenario: Missing locale fallback
- **WHEN** `t("greeting", locale="xx")` is called and locale "xx" has no translations
- **THEN** it SHALL fall back to the system default locale, then to the key itself

### Requirement: Plugin translation registration
Plugins SHALL declare their translation namespaces in `PluginManifest.translations`. When a plugin is registered, its translations SHALL be automatically loaded from the plugin's package directory.

#### Scenario: Plugin declares translations
- **WHEN** a plugin manifest includes `translations=("my-plugin:common",)`
- **THEN** the system SHALL load `locales/{locale}/my-plugin/common.json` from the plugin's package

#### Scenario: Plugin translations override base
- **WHEN** a plugin provides a translation for key `"errors.not_found"` that also exists in the base catalog
- **THEN** the plugin's translation SHALL take precedence

### Requirement: Management API for translation file upload
The system SHALL provide REST endpoints under `/api/i18n/` for managing translations:
- `POST /api/i18n/translations` — upload a translation file (JSON/YAML) for a specific locale and namespace
- `GET /api/i18n/translations/{locale}` — download current translations for a locale
- `GET /api/i18n/locales` — list available locales with metadata
- `PUT /api/i18n/translations/{locale}/{namespace}` — update specific namespace translations

#### Scenario: Upload translation file
- **WHEN** `POST /api/i18n/translations` is called with `locale=zh-CN`, `namespace=common`, and JSON body
- **THEN** the translations SHALL be persisted and immediately available via `t()`

#### Scenario: List available locales
- **WHEN** `GET /api/i18n/locales` is called
- **THEN** it SHALL return a list of locales that have at least one translation file

#### Scenario: Download translations
- **WHEN** `GET /api/i18n/translations/zh-CN` is called
- **THEN** it SHALL return all merged translations for `zh-CN` across all namespaces

### Requirement: Runtime language switching
The system SHALL support runtime language switching. When a user or workspace changes their locale preference, subsequent `t()` calls SHALL use the new locale without restart.

#### Scenario: User changes locale preference
- **WHEN** a user's preferred locale is updated to `"ja"` via the user settings API
- **THEN** subsequent `t()` calls for that user SHALL use Japanese translations

#### Scenario: Workspace changes default locale
- **WHEN** a workspace's default locale is updated to `"de"`
- **THEN** new sessions in that workspace SHALL resolve to German by default

### Requirement: i18n data model for locale preferences
The system SHALL add a `preferred_locale` field to the user model (optional str) and a `default_locale` field to the workspace model (optional str, defaults to `"en"`).

#### Scenario: User has preferred locale
- **WHEN** a user's `preferred_locale` is `"zh-CN"`
- **THEN** the LocaleResolver SHALL use `"zh-CN"` when no explicit locale is provided

#### Scenario: User has no preferred locale
- **WHEN** a user's `preferred_locale` is None
- **THEN** the LocaleResolver SHALL fall back to workspace default, then system default
