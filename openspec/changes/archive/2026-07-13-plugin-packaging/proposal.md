## Why

Hecate's Plugin System (5.5 ✅) loads plugins from a local `plugins/` directory, and TP5 ✅ provides type ABCs + `hecate plugin init` CLI for scaffolding. But there is no way to package a plugin into a distributable bundle, install it from a file, or uninstall it. Developers must manually copy plugin directories. This blocks plugin distribution beyond local development — no upload/install workflow, no version upgrade, no enterprise deployment of plugins across environments.

## What Changes

- **`.hecate-plugin` bundle format**: ZIP archive containing `plugin.yaml` + Python source + `requirements.txt`. Extension of the existing plugin directory structure into a distributable package.
- **`hecate plugin package` CLI**: Packages a plugin directory into a `.hecate-plugin` ZIP bundle. Validates that `plugin.yaml` exists and is well-formed before packaging.
- **`hecate plugin install` CLI**: Installs a `.hecate-plugin` bundle — extracts to `plugins/` directory, installs Python dependencies via `uv pip install -r requirements.txt`, registers in PluginModel DB.
- **`hecate plugin uninstall` CLI**: Removes a plugin — deletes the plugin directory, unregisters from PluginModel DB, unloads from PluginRegistry.
- **Upload/install UI**: Plugin management page gains an "Upload Plugin" button that accepts `.hecate-plugin` files. Backend extracts, installs dependencies, and registers the plugin.
- **Uninstall UI**: Plugin detail page gains an "Uninstall" button.
- **Version management**: Installing a newer version of an existing plugin overwrites the old directory and updates the PluginModel version field.
- **Dependency installation**: After extracting the bundle, `uv pip install -r requirements.txt` is run to install the plugin's Python dependencies.

## Capabilities

### New Capabilities

- `plugin-packaging`: .hecate-plugin ZIP bundle format, packaging CLI, install/uninstall CLI, upload/install/uninstall REST API, upload/uninstall UI, version upgrade workflow, post-install dependency handling

### Modified Capabilities

- `plugin-system`: PluginService gains `install_plugin(bundle_path)` and `uninstall_plugin(plugin_id)` methods; PluginModel may need an `installed_version` field for tracking the installed bundle version

## Impact

- **New files**:
  - `src/hecate/plugin/packaging.py` — bundle creation, extraction, validation
  - `src/hecate/plugin/installer.py` — install/uninstall logic with dependency handling
- **Modified files**:
  - `src/hecate/plugin/cli.py` — add `package`, `install`, `uninstall` subcommands
  - `src/hecate/services/plugin/service.py` — add `install_plugin()`, `uninstall_plugin()`
  - `src/hecate/api/management/plugins.py` — add `POST /upload`, `DELETE /{id}` endpoints
  - `web/src/app/(dashboard)/plugins/page.tsx` — add "Upload Plugin" button
  - `web/src/app/(dashboard)/plugins/[id]/page.tsx` — add "Uninstall" button
