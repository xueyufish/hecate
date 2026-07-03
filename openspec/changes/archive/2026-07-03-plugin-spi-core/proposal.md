## Why

Hecate has 15 extension points (11 Core + 4 SPI), but no unified plugin infrastructure. Each service uses ad-hoc registration patterns: `ChannelTypeRegistry.register()` in engine, `ToolRegistry` in services, `ProviderStrategy.register()` in context, `TaskExecutorRegistry.register()` in scheduling, `AuditSecurityPolicy.register()` in audit. This creates the "N different plugin systems" anti-pattern — no discovery, no lifecycle management, no version compatibility, no dependency resolution.

ADR-016 already decided: "All SPI extensions register through a single `PluginRegistry`." This change implements that decision. Sprint 5's downstream SPI features (ChannelABC, AuthProviderABC, NotifierABC, EvaluatorABC, i18n SPI) are all blocked without a centralized plugin framework.

## What Changes

- **New `src/hecate/plugin/` module** — Plugin SPI Core framework with PluginManifest, PluginRegistry, and PluginLifecycle
- **New `EvaluatorABC`** — Formal evaluation interface that existing 41 evaluators will implement via PluginRegistry
- **Refactor `Evaluator(ABC)`** — Existing evaluator base class becomes `BuiltinEvaluator(EvaluatorABC)`, registered as built-in evaluator plugin type
- **No breaking changes** — Existing evaluator subclasses continue working unchanged

## Capabilities

### New Capabilities

- `plugin-manifest`: PluginManifest dataclass — type, name, version, api_version, min_platform_version, permissions, description
- `plugin-registry`: PluginRegistry — register, unregister, get_by_type, get_by_name, list_all, with thread-safe storage
- `plugin-lifecycle`: PluginLifecycle protocol — on_load, on_unload hooks for plugin initialization and cleanup
- `evaluator-abc`: EvaluatorABC — evaluation plugin interface (name, description, evaluate) registered through PluginRegistry

### Modified Capabilities

- None — this is a new capability addition with no requirement changes to existing features

## Impact

- **New module**: `src/hecate/plugin/` (4 files: `__init__.py`, `manifest.py`, `registry.py`, `lifecycle.py`)
- **Modified**: `src/hecate/services/evaluation/evaluator.py` — Evaluator(ABC) becomes BuiltinEvaluator(EvaluatorABC)
- **Modified**: `src/hecate/services/evaluation/engine.py` — register 41 evaluators via PluginRegistry
- **Tests**: New test files for PluginRegistry, PluginManifest, PluginLifecycle, EvaluatorABC registration
- **Dependencies**: None — pure Python, no external packages required
- **Downstream**: Unblocks Sprint 5 features: ChannelABC (11.1-abc), AuthProviderABC (10.3-abc), NotifierABC (8.6-abc), i18n SPI (15.1)
