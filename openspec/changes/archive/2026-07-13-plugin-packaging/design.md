## Context

Hecate's Plugin System has two layers completed: 5.5 ✅ (runtime engine — plugin.yaml loading, directory discovery, PluginModel DB, config, permissions, REST API, frontend) and TP5 ✅ (8 plugin type ABCs, hecate.plugin SDK, hecate plugin init CLI, hot-reload, API validation, creation UI). Plugins are currently loaded from a local `plugins/` directory — there is no packaging, distribution, or install/uninstall workflow.

**Research basis**: Enterprise platforms (Dify, AgentArts, Versatile, deer-flow, Salesforce) all use ZIP/archive-based package formats for plugin distribution. None use pip or Git URL for enterprise plugin deployment. All support platform-managed installation through UI or API.

## Goals / Non-Goals

**Goals:**
- `.hecate-plugin` ZIP bundle format (plugin.yaml + Python source + requirements.txt)
- `hecate plugin package` CLI — package a directory into a bundle
- `hecate plugin install` CLI — install from a bundle file
- `hecate plugin uninstall` CLI — remove a plugin
- Upload/install UI on plugin management page
- Uninstall UI on plugin detail page
- Post-install dependency handling (`uv pip install -r requirements.txt`)
- Version upgrade (overwrite existing plugin on newer version install)

**Non-Goals:**
- Plugin marketplace/search/discovery — P5 12.0 Asset Marketplace
- Plugin signing/security scanning — P5 5.13 Plugin Security & Signing
- Inter-plugin dependency resolution — no enterprise platform does this
- Plugin rollback to previous version — future enhancement

## Decisions

### Decision 1: ZIP-based `.hecate-plugin` format

**Choice**: ZIP archive containing the plugin directory structure.

**Rationale**: All enterprise platforms (Dify `.difypkg`, deer-flow `.skill`, Salesforce metadata package) use ZIP archives. ZIP is universally supported, works with UI file upload, and Python's `zipfile` module handles it natively without external dependencies.

### Decision 2: Dependency installation via `uv pip install`

**Choice**: After extracting the bundle, run `uv pip install -r requirements.txt` in the venv.

**Rationale**: Hecate already uses `uv` for dependency management. The plugin's `requirements.txt` lists additional Python packages needed by the plugin. Installation happens once at install time, not at every startup.

### Decision 3: Version upgrade = overwrite

**Choice**: Installing a plugin whose name already exists overwrites the old directory and updates the PluginModel version field.

**Rationale**: No enterprise platform does complex version migration for plugins. Simple overwrite is predictable and matches Dify/AgentArts behavior. The `api_version` compatibility check (from 5.5) ensures the new version is compatible with the platform.

### Decision 4: CLI extends existing `hecate plugin` command

**Choice**: Add `package`, `install`, `uninstall` subcommands to the existing `hecate plugin` CLI from TP5.

## Risks / Trade-offs

- **[Malicious packages in requirements.txt]** → A plugin could declare malicious dependencies. Mitigation: 5.13 (P5) will add package signing + security scanning. For now, plugins are installed by trusted administrators.

- **[Dependency conflicts]** → Two plugins requiring different versions of the same package. Mitigation: same as 5.5 — document that plugins should pin compatible versions. Per-plugin virtual environments are future work.

- **[Bundle size]** → Large plugins with many dependencies could produce large bundles. Mitigation: log bundle size at packaging time, warn if > 50MB.
