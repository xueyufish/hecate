## Why

Hecate's Plugin System (5.5 ✅) provides the runtime engine for loading plugins via `plugin.yaml`, but it has no type taxonomy — there are no ABCs defining what a "Tool Plugin" or "Extension Plugin" should implement. The existing SPI ABCs (EvaluatorABC, ChannelABC, AuthProviderABC, SecretProviderABC) were defined ad-hoc in Sprint 4/5 without a unified type system. Third-party developers have no type-safe contracts to build against, and the `hecate.plugin` SDK module doesn't exist. This blocks the developer ecosystem — without defined plugin types, no one can write a plugin.

## What Changes

- **4 new plugin type ABCs**: `ToolPluginABC` (callable function tools), `ExtensionPluginABC` (Guardrail Hook injection — Google ADK BasePlugin pattern with optional `on_pre_llm` / `on_post_llm` / `on_pre_tool` / `on_post_tool` methods), `TriggerPluginABC` (event-driven: webhook/schedule/MQ), `ModelPluginABC` (custom LLM provider based on existing InferenceBackendABC)
- **4 existing ABCs gain plugin.yaml support**: ChannelABC, EvaluatorABC, AuthProviderABC, SecretProviderABC — third parties can now load these via plugin.yaml alongside the existing code-registered built-in providers
- **`hecate.plugin` SDK module**: Type-safe base classes, registration helpers, config injection utilities, permission checking — the developer-facing API for writing plugins
- **`hecate plugin init` CLI**: Template generator that scaffolds a new plugin project (plugin.yaml + Python module + tests skeleton) for any of the 8 types
- **Hot-reload**: File watcher detects plugin.yaml changes during development, re-registers plugins without restart
- **Full `pluginApi` install-time validation**: API surface compatibility checks (SDK version + method signature verification) beyond 5.5's basic version string check
- **API-type plugin online creation UI**: AgentArts-style form-driven UI for creating simple plugins (especially Tool and Trigger types) without writing code
- **Deferred**: Datasource Plugin (overlaps with knowledge base), AgentStrategy Plugin (touches Worker core, P4)

## Capabilities

### New Capabilities

- `plugin-type-taxonomy`: 8 plugin type ABCs (4 new + 4 existing with plugin.yaml support), hecate.plugin SDK module, hecate plugin init CLI, hot-reload, install-time API validation, API-type plugin creation UI

### Modified Capabilities

- `plugin-system`: Plugin loader (from 5.5) gains awareness of plugin types — validates that loaded plugins implement the correct ABC for their declared type

## Impact

- **New files**:
  - `src/hecate/plugin/types/tool.py` — ToolPluginABC
  - `src/hecate/plugin/types/extension.py` — ExtensionPluginABC
  - `src/hecate/plugin/types/trigger.py` — TriggerPluginABC
  - `src/hecate/plugin/types/model.py` — ModelPluginABC
  - `src/hecate/plugin/types/__init__.py` — type registry and exports
  - `src/hecate/plugin/sdk.py` — hecate.plugin SDK module (base classes, helpers)
  - `src/hecate/plugin/cli.py` — `hecate plugin init` CLI
  - `src/hecate/plugin/hot_reload.py` — file watcher for development
  - `src/hecate/plugin/validation.py` — install-time API surface validation
  - `web/src/app/(dashboard)/plugins/create/page.tsx` — API-type plugin creation UI
- **Modified files**:
  - `src/hecate/plugin/loader.py` — type-aware loading (validate ABC match)
  - `src/hecate/plugin/spi/__init__.py` — re-export existing ABCs through unified type system
  - `src/hecate/main.py` — register `hecate plugin` CLI subcommand
  - `web/src/app/(dashboard)/plugins/page.tsx` — add "Create Plugin" button
- **Dependencies**: `watchdog` (file watcher for hot-reload), `click` or `typer` (CLI — check existing)
