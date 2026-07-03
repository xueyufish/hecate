## Context

Hecate has 15 extension points, but no unified plugin infrastructure. Each service uses ad-hoc registration: `ChannelTypeRegistry.register()` (engine), `ToolRegistry` (services), `ProviderStrategy.register()` (context), `TaskExecutorRegistry.register()` (scheduling), `AuditSecurityPolicy.register()` (audit). This creates discovery, lifecycle, and version compatibility problems.

ADR-016 already decided: "All SPI extensions register through a single `PluginRegistry`." The design doc `tool-platform-design.md` shows the target SDK API. This change implements the foundation.

Current state:
- 41 built-in evaluators exist in `services/evaluation/` with `Evaluator(ABC)` base class
- No centralized plugin registry
- No plugin manifest format
- No lifecycle management
- Sprint 5 features (ChannelABC, AuthProviderABC, NotifierABC, EvaluatorABC, i18n SPI) are blocked

## Goals / Non-Goals

**Goals:**
- Provide a centralized PluginRegistry for all SPI extensions
- Define PluginManifest dataclass for plugin metadata
- Define PluginLifecycle protocol for initialization and cleanup hooks
- Create EvaluatorABC as the first SPI type, refactoring existing 41 evaluators to use it
- Follow existing Hecate coding patterns (ABC, type annotations, `from __future__ import annotations`)

**Non-Goals:**
- YAML manifest file parsing (5.5 Plugin System, Sprint 6)
- Plugin sandbox isolation (5.12 MCP Sandbox Security, P4)
- CLI template generator (TP5 Plugin SDK, Sprint 6)
- Hot-reload capability (TP5, Sprint 6)
- 6 plugin type definitions (5.5, Sprint 6)
- Dependency resolution between plugins (5.5, Sprint 6)

## Decisions

### 1. Module Location: `src/hecate/plugin/`

**Decision**: Create new module at `src/hecate/plugin/` with `__init__.py`, `manifest.py`, `registry.py`, `lifecycle.py`.

**Rationale**: Matches design doc convention (`from hecate.plugin import ...`). Clean separation from engine layer. Follows Hecate module organization pattern.

**Alternatives considered**:
- `src/hecate/core/plugin.py` — rejected: too small, doesn't match module convention
- `src/hecate/engine/plugin.py` — rejected: SPI is platform layer, not engine layer

### 2. PluginRegistry Pattern: Class-level singleton with typed registration

**Decision**: `PluginRegistry` is a class with `register(plugin_type: str, name: str, plugin: Any) -> None` and `get_by_type(plugin_type: str) -> dict[str, Any]`. Thread-safe via `threading.Lock`.

**Rationale**: Matches existing `ChannelTypeRegistry` and `ToolRegistry` patterns. Simple, testable, no magic.

**Alternatives considered**:
- Decorator-based auto-registration — rejected: too implicit, harder to test
- Module-level functions — rejected: harder to mock in tests

### 3. PluginManifest: Dataclass, not TypedDict or Pydantic

**Decision**: `@dataclass(frozen=True)` with fields: `type`, `name`, `version`, `api_version`, `min_platform_version`, `description`, `permissions`.

**Rationale**: Engine layer avoids Pydantic dependency. Frozen dataclass is immutable and hashable. Matches `SpanContext` pattern in `ports.py`.

**Alternatives considered**:
- TypedDict — rejected: no validation, not hashable
- Pydantic model — rejected: engine layer dependency constraint

### 4. PluginLifecycle: Protocol, not ABC

**Decision**: `PluginLifecycle` as a `Protocol` with `on_load()` and `on_unload()` methods. Optional for plugins.

**Rationale**: Protocols allow duck typing — plugins don't need to explicitly inherit. Follows Python typing best practices for optional interfaces.

**Alternatives considered**:
- ABC with abstract methods — rejected: too strict, not all plugins need lifecycle hooks
- Callback registration — rejected: less type-safe

### 5. EvaluatorABC: New interface in plugin module, refactor existing Evaluator

**Decision**: Define `EvaluatorABC(ABC)` in `src/hecate/plugin/spi/evaluator.py` with `name`, `description`, `evaluate()`. Existing `Evaluator(ABC)` becomes `BuiltinEvaluator(EvaluatorABC)` that inherits from it. All 41 evaluators register via PluginRegistry.

**Rationale**: Single concept, one place. Third-party evaluators (7.2-abc goal) use same interface. No adapter boilerplate.

**Alternatives considered**:
- Wrap existing Evaluator — rejected: adds adapter layer for no benefit, confusing dual interface

## Risks / Trade-offs

- **Risk**: PluginRegistry could become a bottleneck if many plugins register at startup
  **Mitigation**: Registration is O(n) but happens once at startup. No runtime hot-registration in 5.5a.

- **Risk**: Frozen dataclass for PluginManifest makes future extension harder
  **Mitigation**: Add optional fields with defaults. New fields can be added non-breakingly.

- **Risk**: Refactoring Evaluator(ABC) could break existing 41 evaluator subclasses
  **Mitigation**: BuiltinEvaluator inherits from EvaluatorABC, preserving existing interface. Subclasses only need to change import path if they reference Evaluator directly.

- **Trade-off**: Protocol for PluginLifecycle means no enforcement at class definition time
  **Mitigation**: PluginRegistry checks for lifecycle methods at registration time and logs warning if missing.

## Migration Plan

1. Create `src/hecate/plugin/` module with PluginManifest, PluginRegistry, PluginLifecycle
2. Create `src/hecate/plugin/spi/evaluator.py` with EvaluatorABC
3. Refactor `services/evaluation/evaluator.py`: Evaluator → BuiltinEvaluator(EvaluatorABC)
4. Update `services/evaluation/engine.py` to register all 41 evaluators via PluginRegistry
5. Update existing evaluator imports if needed (keep backward compatibility)
6. Run `ruff check src/hecate/ tests/ && ruff format --check src/ tests/ && mypy src/ && python -m pytest tests/ -q`

## Open Questions

- Should PluginRegistry have a `get_all()` method or just `get_by_type()`? — Decided: both, for completeness
- Should PluginManifest.permissions be a list of strings or a more structured type? — Decided: list of strings for simplicity, can be extended later
- Should we add a `@register_plugin` decorator for convenience? — Decided: not in 5.5a, add in 5.5 if needed
