## Context

Hecate's Plugin System (5.5 ✅) provides the runtime engine: `plugin.yaml` loading, directory discovery, `PluginModel` DB, config management, REST API, and frontend. But it has no type taxonomy — there are no ABCs defining what a Tool Plugin or Extension Plugin should implement.

The existing SPI ABCs were defined ad-hoc:
- `EvaluatorABC` (plugin/spi/evaluator.py)
- `ChannelABC` (channel/adapter.py)
- `AuthProviderABC` (auth/provider.py)
- `SecretProviderABC` (vault/provider.py)
- `PreLLMHook / PostLLMHook / PreToolHook / PostToolHook` (engine/guardrail.py)
- `InferenceBackendABC` (model_hub/inference_manager.py)

These are scattered across the codebase without a unified type system. Third-party developers have no single entry point (`hecate.plugin` module) or template generator (`hecate plugin init`) to start building plugins.

**Research basis**: Analyzed 14 platforms (Dify, Claude Code, OpenClaw, Google ADK, AgentScope, deer-flow, HermesAgent, watsonx, Bedrock, Salesforce, Palantir AIP, AgentArts, Versatile, openjiuwen). Key findings:
- Tool Plugin appears in 14/14 platforms
- Hook/Extension appears in 10/14 platforms
- Google ADK's `BasePlugin` (one class with optional callback methods) is the cleanest hook design
- HermesAgent's "general plugin + specialized types" model maps well to Hecate

## Goals / Non-Goals

**Goals:**
- Define 8 plugin types classified by capability (4 new ABCs + 4 existing ABCs with plugin.yaml support)
- Create `hecate.plugin` SDK module with type-safe base classes
- Create `hecate plugin init` CLI template generator
- Hot-reload during development
- Full install-time API surface validation
- API-type plugin online creation UI

**Non-Goals:**
- Datasource Plugin — deferred (overlaps with knowledge base system)
- AgentStrategy Plugin — deferred to P4 (touches Worker core architecture)
- Plugin packaging/distribution — that's 5.5b
- Plugin signing/security — that's 5.13 (P5)

## Decisions

### Decision 1: ExtensionPluginABC uses Google ADK BasePlugin pattern

**Choice**: One class with optional callback methods (`on_pre_llm`, `on_post_llm`, `on_pre_tool`, `on_post_tool`), not 4 separate Hook ABCs.

**Rationale**: Google ADK's BasePlugin is the cleanest design — developers write one class, override only the methods they need. The existing PreLLMHook/PostLLMHook/PreToolHook/PostToolHook continue as internal engine interfaces; ExtensionPluginABC is the user-facing wrapper.

**Alternatives considered**:
- No new ABC, plugin.yaml declares which Hook interfaces it implements: Less type-safe, developers must understand 4 separate ABCs.
- Dynamic registration like OpenClaw/HermesAgent (`api.registerHook("pre_tool_call", callback)`): Too loose for Hecate's strict-type philosophy.

### Decision 2: Built-in providers stay code-registered, third-party uses plugin.yaml

**Choice**: Existing `register_auth_providers()`, `register_channels()` etc. continue as-is. TP5 adds plugin.yaml support so third parties can load NEW instances of the same type.

**Rationale**: Chicken-and-egg — Auth Providers must be available before the plugin loading system starts (API requests need authentication). Also, built-in providers need access to internal config (e.g., `settings.JWT_SECRET`) that plugins shouldn't have. This matches AgentArts (预置 + 自定义), Salesforce (standard + custom actions), OpenClaw (bundled + installed).

### Decision 3: TriggerPluginABC supports 3 trigger sources

**Choice**: `webhook` (HTTP POST → handler), `schedule` (cron expression → handler), `event` (internal event bus → handler). Based on Versatile's 4 trigger types (Webhook/Schedule/Event/MCP), minus MCP (handled by MCP Client 5.3).

### Decision 4: hecate.plugin SDK module structure

**Choice**: Single import path `from hecate.plugin import ToolPluginABC, ExtensionPluginABC, ...`. All 8 type ABCs re-exported from one module. SDK also provides `PluginContext` for config injection and `register()` helper.

### Decision 5: CLI uses click (already a dependency)

**Choice**: `hecate plugin init <name> --type <type>` scaffolds plugin directory. Uses click for CLI parsing (check if already a dependency; if not, use argparse to avoid new dependency).

## Risks / Trade-offs

- **[ExtensionPluginABC parallel to existing Hooks]** → Two interfaces for the same concept. Mitigation: ExtensionPluginABC is a thin wrapper; internal engine continues using Hook ABCs directly. The bridge is in the loader.

- **[Trigger Plugin scope creep]** → Event-driven triggers could grow complex (message queues, event sourcing). Mitigation: TP5 only does webhook + schedule + simple event bus. MQ integration is future work.

- **[Hot-reload reliability]** → File watching in async Python can be tricky. Mitigation: Use `watchdog` library (mature, cross-platform). Only enable in development mode (not production).

- **[API validation complexity]** → Full API surface validation requires introspecting plugin classes at install time. Mitigation: Start with method signature checking (has expected methods with correct params). Deep type annotation checking is future work.
