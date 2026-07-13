## Why

Hecate's Plugin SPI Core (5.5a) provides `PluginRegistry`, `PluginManifest`, and `PluginLifecycle` for in-process plugin registration, but all existing SPI implementations (EvaluatorABC, ChannelABC, AuthProviderABC, SecretProviderABC) are registered via hardcoded Python functions (`register_auth_providers()`, `register_channels()`). There is no declarative plugin loading mechanism — no `plugin.yaml` manifest parsing, no directory discovery, no configuration management, no permission enforcement, and no UI for plugin lifecycle management. This blocks third-party plugin development and limits the platform to built-in providers only.

## What Changes

- **Plugin manifest loading**: Parse `plugin.yaml` files into `PluginManifest` objects with validation (required fields, `api_version` / `min_platform_version` compatibility checks)
- **Directory discovery**: Scan `plugins/` directory for plugin packages, auto-discover and register plugins at startup
- **Extended PluginLifecycle**: Add `on_enable`, `on_disable`, `on_config_change` hooks to the existing `on_load` / `on_unload` protocol
- **PluginModel DB table**: New ORM model persisting plugin state (installed, enabled, error), per-workspace enablement, and configuration values — DB is the runtime source of truth
- **Configuration management**: `config_schema` (JSON Schema) in `plugin.yaml` → DB storage → runtime injection into plugin instances; frontend auto-generates config forms from schema
- **Permission declaration and enforcement**: Plugins declare required permissions in `plugin.yaml`; loader rejects undeclared permissions at runtime
- **Entry loading strategies**: `python:module:Class` (in-process importlib) and `mcp://endpoint` (via existing MCP Client 5.3) — in-process + MCP hybrid architecture, no custom daemon
- **REST API**: `GET /api/plugins` (list), `POST /api/plugins/{id}/enable`, `POST /api/plugins/{id}/disable`, `PUT /api/plugins/{id}/config` (update configuration)
- **Frontend plugin management page**: Plugin list with status badges, enable/disable toggles, config form auto-generated from `config_schema`, MCP endpoint connection management UI
- **Two-layer scope**: Platform-level plugins (shipped with Hecate, globally available) + workspace-level plugins (per-workspace install and enablement)

## Capabilities

### New Capabilities

- `plugin-system`: Plugin runtime engine — manifest loading, directory discovery, compatibility validation, extended lifecycle, DB-backed state management, configuration injection, permission enforcement, entry loading (in-process + MCP), REST API, and frontend management UI

### Modified Capabilities

(None — existing SPI registration mechanism via `register_auth_providers()` etc. remains unchanged; the plugin system provides an additional declarative loading path alongside the existing imperative path)

## Impact

- **New files**:
  - `src/hecate/plugin/loader.py` — plugin.yaml parser + directory scanner + entry loader
  - `src/hecate/plugin/config.py` — config schema validation + runtime injection
  - `src/hecate/plugin/permission.py` — permission declaration parsing + enforcement
  - `src/hecate/models/plugin.py` — `PluginModel` ORM (id, name, type, version, status, config, workspace_id)
  - `src/hecate/api/management/plugins.py` — REST API router
  - `alembic/versions/xxx_add_plugin_model.py` — DB migration
  - `web/src/app/(dashboard)/plugins/page.tsx` — frontend management page
  - `web/src/app/(dashboard)/plugins/[id]/page.tsx` — plugin detail + config page
- **Modified files**:
  - `src/hecate/plugin/lifecycle.py` — add `on_enable` / `on_disable` / `on_config_change` to `PluginLifecycle` protocol
  - `src/hecate/plugin/manifest.py` — add `entry`, `permissions`, `config_schema` fields to `PluginManifest`
  - `src/hecate/main.py` — register plugins router, trigger plugin discovery at startup
  - `src/hecate/core/config.py` — add `PLUGINS_DIR` setting
  - `web/src/components/sidebar.tsx` — add "Plugins" navigation link
- **Dependencies**: `yaml` (already installed), `jsonschema` (already installed for Graph DSL)
- **DB migration**: New `plugins` table with workspace_id FK
