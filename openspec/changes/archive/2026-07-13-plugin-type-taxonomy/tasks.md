## 1. New Plugin Type ABCs

- [x] 1.1 Create `src/hecate/plugin/types/tool.py` — `ToolPluginABC` with `name`, `description` properties and async `execute(params: dict) -> dict` method
- [x] 1.2 Create `src/hecate/plugin/types/extension.py` — `ExtensionPluginABC` with optional callbacks `on_pre_llm`, `on_post_llm`, `on_pre_tool`, `on_post_tool` (Google ADK BasePlugin pattern)
- [x] 1.3 Create `src/hecate/plugin/types/trigger.py` — `TriggerPluginABC` with `trigger_type` (webhook/schedule/event), async `on_webhook`, `on_schedule`, `on_event` methods
- [x] 1.4 Create `src/hecate/plugin/types/model.py` — `ModelPluginABC` with async `invoke(messages, config) -> dict` and `embed(text) -> list[float]` methods
- [x] 1.5 Create `src/hecate/plugin/types/__init__.py` — type registry mapping type strings to ABCs, re-export all 4 new ABCs + 4 existing ABCs (ChannelABC, EvaluatorABC, AuthProviderABC, SecretProviderABC)

## 2. SDK Module

- [x] 2.1 Create `src/hecate/plugin/sdk.py` — `hecate.plugin` SDK module: re-export all 8 type ABCs from single import path, `PluginContext` class (config access + permission checking), `register()` helper function
- [x] 2.2 Update `src/hecate/plugin/__init__.py` to re-export SDK symbols for `from hecate.plugin import ToolPluginABC`

## 3. Type-Aware Loader

- [x] 3.1 Update `src/hecate/plugin/loader.py` — `load_plugin()` validates that loaded entry class implements the correct ABC for its declared `type` field. Add `_validate_type(manifest, plugin_instance)` that checks `isinstance` against the type registry
- [x] 3.2 Add type validation errors to existing error handling (reject plugins with wrong ABC, log clearly)

## 4. Install-Time API Validation

- [x] 4.1 Create `src/hecate/plugin/validation.py` — `validate_api_surface(manifest, plugin_instance) -> list[str]` that checks method signatures match the expected ABC contract (e.g., Tool Plugin must have `execute` method). Returns list of validation errors (empty = valid)
- [x] 4.2 Integrate `validate_api_surface` into loader — call after `_validate_type`, reject plugin if errors found

## 5. CLI Template Generator

- [x] 5.1 Create `src/hecate/plugin/cli.py` — `hecate plugin init <name> --type <type>` command using click or argparse. Accepts all 8 types. Scaffolds: `plugin.yaml`, `__init__.py` (with correct ABC subclass), `test_<name>.py`
- [x] 5.2 Register CLI command in `src/hecate/main.py` or as standalone `hecate` CLI entry point
- [x] 5.3 Add `HOT_RELOAD: bool = False` setting to `src/hecate/core/config.py`

## 6. Hot-Reload

- [x] 6.1 Add `watchdog` to `[dev]` optional dependencies in `pyproject.toml`
- [x] 6.2 Create `src/hecate/plugin/hot_reload.py` — `PluginHotReloader` class using `watchdog.Observer` to watch plugins directory. On file change: unload old plugin, reload new, update PluginRegistry. Only active when `settings.HOT_RELOAD=True`
- [x] 6.3 Integrate hot-reloader into startup — start observer if `HOT_RELOAD=True`, log "Hot-reload enabled"

## 7. Extension Plugin Bridge to Guardrail Hooks

- [x] 7.1 Create bridge logic in `src/hecate/plugin/types/extension.py` — `ExtensionPluginAdapter` class that wraps an `ExtensionPluginABC` instance and exposes it as `PreLLMHook` / `PostLLMHook` / `PreToolHook` / `PostToolHook` to the existing engine guardrail system
- [x] 7.2 Register adapter with engine's guardrail config when Extension Plugin is enabled

## 8. Backend Tests

- [x] 8.1 Test `ToolPluginABC` — verify subclass creates valid tool, verify abstract methods enforced
- [x] 8.2 Test `ExtensionPluginABC` — verify partial callbacks work (only `on_pre_tool` implemented → others skipped)
- [x] 8.3 Test `ExtensionPluginAdapter` bridge — verify it correctly delegates to existing Hook system
- [x] 8.4 Test `TriggerPluginABC` — verify webhook and schedule trigger types
- [x] 8.5 Test `ModelPluginABC` — verify invoke and embed methods
- [x] 8.6 Test type-aware loader — verify correct ABC validated for each type, verify rejection on mismatch
- [x] 8.7 Test `validate_api_surface` — verify method signature checking catches missing methods
- [x] 8.8 Test `hecate plugin init` CLI — verify scaffolding for each of 8 types, verify invalid type rejected
- [x] 8.9 Test hot-reload — verify file change triggers reload (mock file watcher)
- [x] 8.10 Test SDK module imports — verify `from hecate.plugin import ToolPluginABC` works

## 9. Frontend — API-Type Plugin Creation UI

- [x] 9.1 Create `web/src/app/(dashboard)/plugins/create/page.tsx` — plugin creation form with type selector (Tool/Trigger), parameter definition table, API endpoint URL field (for Tool), webhook path + cron expression fields (for Trigger)
- [x] 9.2 Add "Create Plugin" button to `web/src/app/(dashboard)/plugins/page.tsx` — links to create page
- [x] 9.3 Create backend API endpoint `POST /api/plugins/create` — accepts plugin definition, creates PluginModel with generated manifest

## 10. Verification

- [x] 10.1 Run `ruff check src/hecate/ tests/ && ruff format --check src/ tests/` — 0 errors
- [x] 10.2 Run `mypy src/` — 0 errors
- [x] 10.3 Run `python -m pytest tests/test_plugin/ -q` — all pass (80/80)
- [ ] 10.4 Manual verification: run `hecate plugin init test-tool --type tool`, verify scaffold, load it, verify it appears in UI
