## 1. Plugin Manifest Extension

- [x] 1.1 Add `entry: str`, `permissions: tuple[str, ...]`, `config_schema: dict[str, Any] | None` fields to `PluginManifest` in `src/hecate/plugin/manifest.py`. Update `__post_init__` to convert list-type `permissions` to tuple for immutability.
- [x] 1.2 Add `PLUGINS_DIR: str = "./plugins"` setting to `src/hecate/core/config.py`

## 2. Extended PluginLifecycle

- [x] 2.1 Add `on_enable`, `on_disable`, `on_config_change` methods to `PluginLifecycle` Protocol in `src/hecate/plugin/lifecycle.py`. All three are optional (Protocol structural typing — `@runtime_checkable` + `hasattr` detection).
- [x] 2.2 Update `PluginRegistry.register()` to call `on_load` if implemented (existing behavior, verify still works with extended protocol)

## 3. Plugin Loader

- [x] 3.1 Create `src/hecate/plugin/loader.py` with `PluginLoader` class:
  - `discover_plugins(plugins_dir: Path) -> list[PluginManifest]` — scan directory for `plugin.yaml` files, parse each into `PluginManifest`
  - `load_plugin(manifest: PluginManifest) -> Any` — dispatch to `_load_python()` or `_load_mcp()` based on `entry` prefix
  - `_load_python(entry: str) -> Any` — `importlib.import_module(module)` then `getattr(module, cls)()` instantiation
  - `_load_mcp(entry: str) -> Any` — connect via MCP Client, discover tools, return wrapped proxy
  - `_validate_compatibility(manifest: PluginManifest) -> None` — check `api_version` and `min_platform_version` against current platform version
- [x] 3.2 Handle loader errors: catch `ImportError`, `yaml.YAMLError`, `ValueError`, log with `logger.exception()`, do not crash startup

## 4. Plugin Configuration Management

- [x] 4.1 Create `src/hecate/plugin/config.py` with:
  - `validate_config(config: dict, schema: dict) -> None` — validate config dict against JSON Schema using `jsonschema.validate()`, raise `ValidationError` on failure
  - `inject_config(plugin_instance: Any, config: dict) -> None` — inject config into plugin instance (call `on_config_change` if implemented, else set attribute)

## 5. Permission Enforcement

- [x] 5.1 Create `src/hecate/plugin/permission.py` with:
  - `PermissionChecker` class that takes declared permissions from manifest
  - `check_permission(permission: str) -> bool` — returns True if permission is declared
  - `log_undeclared(permission: str, plugin_name: str) -> None` — logs warning for undeclared permission usage

## 6. PluginModel DB Table

- [x] 6.1 Create `src/hecate/models/plugin.py` with `PluginModel`:
  - `id: UUID` (PK), `name: str`, `type: str`, `version: str`, `status: str` (enum: installed/enabled/disabled/error), `entry: str`, `manifest: dict` (JSON), `config: dict` (JSON), `workspace_id: UUID | None` (nullable, None = platform-level), standard timestamps (`created_at`, `updated_at`, `deleted_at`)
  - Inherit `BaseModel` (not `Base`), follow existing model conventions
- [x] 6.2 Create Alembic migration `alembic/versions/xxx_add_plugin_model.py` to add `plugins` table

## 7. Plugin Service

- [x] 7.1 Create `src/hecate/services/plugin/service.py` with `PluginService`:
  - `list_plugins(workspace_id: UUID | None) -> list[PluginModel]` — return platform-level + workspace-level plugins
  - `get_plugin(plugin_id: UUID) -> PluginModel`
  - `enable_plugin(plugin_id: UUID) -> None` — update status to `enabled`, call `on_enable` if implemented
  - `disable_plugin(plugin_id: UUID) -> None` — update status to `disabled`, call `on_disable` if implemented
  - `update_config(plugin_id: UUID, config: dict) -> None` — validate against `config_schema`, persist, call `on_config_change`
  - `register_discovered_plugins(plugins_dir: Path) -> int` — discover + load + persist plugins at startup

## 8. REST API

- [x] 8.1 Create `src/hecate/api/management/plugins.py` router with prefix `/api/plugins`:
  - `GET /` — list plugins (query param: `workspace_id` optional)
  - `GET /{plugin_id}` — get plugin detail
  - `POST /{plugin_id}/enable` — enable plugin
  - `POST /{plugin_id}/disable` — disable plugin
  - `PUT /{plugin_id}/config` — update plugin configuration (body: config dict)
- [x] 8.2 Register `plugins_router` in `src/hecate/main.py`
- [x] 8.3 Add Pydantic schemas: `PluginReadSchema`, `PluginConfigUpdateSchema` in `src/hecate/api/management/plugins.py`

## 9. Startup Integration

- [x] 9.1 In `src/hecate/main.py` startup event: call `PluginService.register_discovered_plugins(settings.PLUGINS_DIR)` to discover and register plugins from the plugins directory
- [x] 9.2 Log summary: "Discovered N plugins, M enabled, K errors"

## 10. Backend Tests

- [x] 10.1 Test `PluginLoader.discover_plugins()` — create temp `plugins/` dir with valid and invalid plugin.yaml files, verify discovery results
- [x] 10.2 Test `PluginLoader._load_python()` — verify importlib loading of a test plugin module, verify error handling for nonexistent module
- [x] 10.3 Test `_validate_compatibility()` — verify version comparison accepts compatible and rejects incompatible plugins
- [x] 10.4 Test `PluginService.enable_plugin()` / `disable_plugin()` — verify status transitions and lifecycle hook invocation
- [x] 10.5 Test `PluginService.update_config()` — verify JSON Schema validation accepts valid config and rejects invalid config
- [x] 10.6 Test two-layer scope — verify platform-level plugins visible to all workspaces, workspace-level plugins isolated
- [x] 10.7 Test REST API endpoints — list, get, enable, disable, update config via `httpx.AsyncClient`

## 11. Frontend — Plugin List Page

- [x] 11.1 Create `web/src/app/(dashboard)/plugins/page.tsx` — plugin list table with columns: name, type, version, status badge (enabled=green, disabled=gray, error=red), workspace scope indicator
- [x] 11.2 Add enable/disable toggle button per plugin row — calls `POST /api/plugins/{id}/enable` or `POST /api/plugins/{id}/disable`
- [x] 11.3 Add "Plugins" link to `web/src/components/sidebar.tsx` pointing to `/plugins`

## 12. Frontend — Plugin Detail & Config Page

- [x] 12.1 Create `web/src/app/(dashboard)/plugins/[id]/page.tsx` — plugin detail page showing: manifest info (name, type, version, description, entry, permissions), connection status (for MCP plugins), config form
- [x] 12.2 Implement config form auto-generation from `config_schema` — render fields based on JSON Schema types:
  - `string` with `secret: true` or `format: password` → password input
  - `string` with `enum` → dropdown select
  - `string` → text input
  - `number` / `integer` with `minimum` / `maximum` → number input with bounds
  - `boolean` → toggle switch
  - `description` → field label / tooltip
- [x] 12.3 Config form save button — calls `PUT /api/plugins/{id}/config`, shows success/error toast

## 13. Verification

- [x] 13.1 Run `ruff check src/hecate/ tests/ && ruff format --check src/ tests/` — 0 errors
- [x] 13.2 Run `mypy src/` — 0 errors
- [x] 13.3 Run `python -m pytest tests/test_plugin/ -q` — all pass
- [ ] 13.4 Manual verification: create a test `plugins/` dir with a sample plugin.yaml, start the application, verify plugin appears in UI, can be enabled/disabled/configured
