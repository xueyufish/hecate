## Context

Hecate's Plugin SPI Core (5.5a, archived) established the registration layer: `PluginRegistry`, `PluginManifest` (frozen dataclass), and `PluginLifecycle` (Protocol with `on_load`/`on_unload`). Four SPI ABCs are registered through it: `EvaluatorABC`, `ChannelABC`, `AuthProviderABC`, `SecretProviderABC`.

However, all registration is imperative — hardcoded Python functions like `register_auth_providers()` create instances and call `registry.register()` directly. There is no declarative path: no `plugin.yaml` manifest parsing, no directory scanning, no configuration injection, no permission enforcement, no DB persistence, and no management UI.

This change adds the **plugin runtime engine** that bridges the gap between imperative registration and declarative plugin loading, configuration, and management.

**Competitive analysis basis**: Researched 14 platforms (Dify, Claude Code, OpenClaw, Google ADK, AgentScope, deer-flow, watsonx, Bedrock AgentCore, Salesforce Agentforce, Palantir AIP, Huawei AgentArts, Huawei Versatile, openjiuwen, HermesAgent). Key decisions below are grounded in this analysis.

## Goals / Non-Goals

**Goals:**
- Declarative plugin loading via `plugin.yaml` manifest
- Local directory discovery (`plugins/` scan at startup)
- Basic `api_version` / `min_platform_version` compatibility validation
- Extended lifecycle hooks: `on_enable`, `on_disable`, `on_config_change`
- DB-backed plugin state with per-workspace enablement (two-layer scope: platform + workspace)
- Configuration management: `config_schema` (JSON Schema) → DB → runtime injection
- Permission declaration in manifest + runtime enforcement
- Entry loading: `python:module:Class` (in-process importlib) + `mcp://endpoint` (via MCP Client 5.3)
- REST API for plugin management (list, enable, disable, configure)
- Frontend plugin management page with auto-generated config forms

**Non-Goals (deferred):**
- 6 plugin type ABCs (Tool/Trigger/Extension/Model/Datasource/AgentStrategy) → TP5
- `hecate.plugin` Python SDK module → TP5
- `hecate plugin init` CLI template generator → TP5
- Hot-reload during development → TP5
- Full `pluginApi` install-time API surface validation → TP5
- API-type plugin online creation UI → TP5
- Plugin packaging format (`.hecate-plugin` bundle) → 5.5b
- Packaging CLI, upload/install/uninstall UI → 5.5b
- Version management + upgrade workflow → 5.5b
- Plugin signing and security scanning → 5.13 (P5)
- Out-of-process daemon / WASM isolation → P5
- Marketplace distribution → P5 (12.0)

## Decisions

### Decision 1: In-process + MCP hybrid (no custom daemon)

**Choice**: Plugins load in-process via `importlib`. Remote plugins connect via existing MCP Client. No custom Plugin Daemon.

**Rationale**: Researched 14 platforms. The industry trend is MCP replacing custom daemon protocols (Bedrock AgentCore and Huawei Versatile are MCP-first). Dify's daemon was a 2024 solution before MCP matured. OpenClaw proves multi-tenant + in-process is viable with proper manifest contracts.

**Alternatives considered**:
- Dify-style daemon (subprocess + stdio/TCP IPC): Rejected — doubles effort (IPC protocol + SPI Proxy layer + daemon process manager), and MCP already handles the "remote plugin" case.
- Bedrock-style all-MCP: Rejected — forces JSON-RPC overhead for trivial local operations (e.g., JWT auth verification on every request).
- Hybrid daemon + in-process: Rejected — maintaining two execution paths adds complexity without clear benefit when MCP covers remote access.

### Decision 2: DB as runtime source of truth

**Choice**: `PluginModel` DB table stores plugin state (status, config, workspace_id). `plugin.yaml` is the development-time declaration; at install/registration time manifest data is written to DB. Runtime reads from DB exclusively.

**Rationale**: All enterprise platforms with web UI (AgentArts, Versatile, Salesforce, Palantir) use DB as the single source of truth. File-based config (OpenClaw) only works for CLI-driven tools without web management.

**Schema** (simplified):
```
PluginModel:
  id: UUID (PK)
  name: str (unique per workspace)
  type: str
  version: str
  status: enum(installed, enabled, disabled, error)
  entry: str  # "python:module:Class" or "mcp://endpoint"
  manifest: JSON  # full plugin.yaml content
  config: JSON  # runtime config values (validated against config_schema)
  workspace_id: UUID | None  # None = platform-level plugin
  created_at, updated_at, deleted_at
```

### Decision 3: Two-layer scope (platform + workspace)

**Choice**: Platform-level plugins (shipped with Hecate, `workspace_id=None`) are globally available. Workspace-level plugins are installed per-workspace.

**Rationale**: AgentArts, Versatile, Salesforce all use this two-layer model — official pre-installed plugins (global) + user-created plugins (per-workspace/org).

### Decision 4: Permission declaration + enforcement (Dify model)

**Choice**: Plugins declare permissions in `plugin.yaml` (`permissions: [network:https, filesystem:read]`). Loader validates at registration. Runtime enforces — undeclared permissions are rejected.

**Rationale**: Dify's research found sandboxing hurts plugin developer experience (dependency restrictions). Signature + permission declaration is the pragmatic alternative. Deep signing/scanning deferred to 5.13 (P5).

### Decision 5: config_schema → UI auto-generation

**Choice**: `config_schema` in `plugin.yaml` uses JSON Schema. Backend validates config against schema on save. Frontend auto-generates form fields from schema (string→text input, secret string→password, enum→dropdown, number with min/max→number input with bounds).

**Rationale**: AgentArts uses input/output parameter forms driven by schema definitions. Salesforce Apex actions auto-generate parameter forms from InvocableVariable descriptions. This is the enterprise platform standard pattern.

### Decision 6: Extended PluginLifecycle via optional protocol methods

**Choice**: Extend `PluginLifecycle` Protocol with `on_enable`, `on_disable`, `on_config_change`. Plugins that don't implement these are unaffected (Protocol structural typing).

```python
class PluginLifecycle(Protocol):
    def on_load(self) -> None: ...
    def on_unload(self) -> None: ...
    # New optional hooks
    def on_enable(self) -> None: ...
    def on_disable(self) -> None: ...
    def on_config_change(self, new_config: dict[str, Any]) -> None: ...
```

**Rationale**: Enables runtime enable/disable without full reload, and config hot-update without re-registration. `@runtime_checkable` on the Protocol allows the loader to detect which hooks a plugin implements via `hasattr`.

## Risks / Trade-offs

- **[In-process crash propagation]** → A plugin exception could crash the main process. Mitigation: loader wraps `on_load`/`on_enable` in try/except, logs errors, marks plugin status as `error` in DB. Consumer code (PluginRegistry callers) already wraps plugin calls in try/except for existing SPIs.

- **[Dependency conflicts between plugins]** → Two plugins requiring different versions of the same package. Mitigation: document that plugins should pin compatible versions. Per-plugin virtual environments are a future enhancement (not 5.5). For now, plugin developers must ensure compatibility.

- **[Permission enforcement granularity]** → Permission declaration is coarse-grained (`network:https`, `filesystem:read`). Not a capability-based security model. Mitigation: this matches Dify's pragmatic model. Fine-grained capability-based security (WASM) is P5.

- **[Plugin discovery performance]** → Scanning `plugins/` directory on every startup could be slow with many plugins. Mitigation: cache discovered manifests in DB; only re-scan if directory mtime changed.

- **[Config schema validation overhead]** → JSON Schema validation on every config save. Mitigation: `jsonschema` is already a dependency (Graph DSL). Config saves are infrequent (admin operation), not a hot path.
