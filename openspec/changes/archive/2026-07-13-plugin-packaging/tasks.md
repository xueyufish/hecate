## 1. Plugin Bundle Format

- [x] 1.1 Create `src/hecate/plugin/packaging.py` with:
  - `create_bundle(plugin_dir: Path, output_path: Path | None = None) -> Path` — validate plugin.yaml exists, ZIP the directory contents, output `.hecate-plugin` file
  - `extract_bundle(bundle_path: Path, target_dir: Path) -> Path` — extract ZIP to target_dir, return extracted plugin directory path
  - `validate_bundle(bundle_path: Path) -> bool` — check file is valid ZIP containing plugin.yaml

## 2. Plugin Installer

- [x] 2.1 Create `src/hecate/plugin/installer.py` with:
  - `install_plugin(bundle_path: Path, plugins_dir: Path) -> str` — extract bundle, install deps via `uv pip install -r requirements.txt`, return plugin name
  - `uninstall_plugin(plugin_name: str, plugins_dir: Path) -> bool` — delete plugin directory, return True if deleted
  - `_install_dependencies(plugin_dir: Path) -> None` — run `uv pip install -r requirements.txt` if requirements.txt exists

## 3. CLI Extensions

- [x] 3.1 Add `package` subcommand to `src/hecate/plugin/cli.py` — `hecate plugin package <dir>` calls `create_bundle()`
- [x] 3.2 Add `install` subcommand to `src/hecate/plugin/cli.py` — `hecate plugin install <file.hecate-plugin>` calls `install_plugin()`
- [x] 3.3 Add `uninstall` subcommand to `src/hecate/plugin/cli.py` — `hecate plugin uninstall <name>` calls `uninstall_plugin()`

## 4. Service Layer

- [x] 4.1 Add `install_plugin(bundle_path: str) -> PluginModel` to `PluginService` — calls installer, creates/updates PluginModel, loads plugin via PluginLoader
- [x] 4.2 Add `uninstall_plugin(plugin_id: uuid.UUID) -> None` to `PluginService` — calls installer to delete directory, deletes PluginModel record. Reject if plugin is built-in (workspace_id is None AND status is not "installed" via plugin.yaml)

## 5. REST API

- [x] 5.1 Add `POST /api/plugins/upload` endpoint to `src/hecate/api/management/plugins.py` — accepts multipart file upload (.hecate-plugin), saves to temp, calls `PluginService.install_plugin()`, returns PluginReadSchema
- [x] 5.2 Add `DELETE /api/plugins/{id}` endpoint — calls `PluginService.uninstall_plugin()`, returns 200. Reject built-in plugins with 403

## 6. Frontend — Upload UI

- [x] 6.1 Add "Upload Plugin" button to `web/src/app/(dashboard)/plugins/page.tsx` — opens hidden file input for `.hecate-plugin` files, POSTs to `/api/plugins/upload`, refreshes list on success
- [x] 6.2 Show upload progress / error toast

## 7. Frontend — Uninstall UI

- [x] 7.1 Add "Uninstall" button to `web/src/app/(dashboard)/plugins/[id]/page.tsx` — calls `DELETE /api/plugins/{id}`, redirects to plugin list on success
- [x] 7.2 Hide "Uninstall" button for built-in plugins (workspace_id is null AND entry starts with "python:hecate.")

## 8. Backend Tests

- [x] 8.1 Test `create_bundle()` — create temp plugin dir with plugin.yaml, package it, verify ZIP contents
- [x] 8.2 Test `create_bundle()` rejects dir without plugin.yaml
- [x] 8.3 Test `extract_bundle()` — extract a valid bundle, verify plugin.yaml and source files present
- [x] 8.4 Test `install_plugin()` — install a bundle to temp plugins dir, verify PluginModel created
- [x] 8.5 Test `uninstall_plugin()` — uninstall an installed plugin, verify directory deleted and PluginModel removed
- [x] 8.6 Test install upgrade — install v1, then install v2 with same name, verify version updated
- [x] 8.7 Test `POST /api/plugins/upload` via httpx AsyncClient
- [x] 8.8 Test `DELETE /api/plugins/{id}` — verify uninstall via API, verify 403 for built-in plugins

## 9. Verification

- [x] 9.1 Run `ruff check src/hecate/ tests/ && ruff format --check src/ tests/` — 0 errors
- [x] 9.2 Run `mypy src/` — 0 errors
- [x] 9.3 Run `python -m pytest tests/test_plugin/ -q` — all pass
- [x] 9.4 Manual verification: `hecate plugin init test-tool --type tool`, `hecate plugin package ./test-tool`, `hecate plugin install test-tool.hecate-plugin`, verify appears in UI, then uninstall
