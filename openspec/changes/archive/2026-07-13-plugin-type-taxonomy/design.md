## Context — 背景

Hecate's Plugin System (5.5 ✅) provides the runtime engine: `plugin.yaml` loading, directory discovery, `PluginModel` DB, config management, REST API, and frontend. But it has no type taxonomy — there are no ABCs defining what a Tool Plugin or Extension Plugin should implement.

Hecate 的插件系统（5.5 ✅）提供运行时引擎：`plugin.yaml` 加载、目录发现、`PluginModel` DB、配置管理、REST API 和前端。但它没有类型分类法 — 没有定义工具插件或扩展插件应实现的 ABC。

The existing SPI ABCs were defined ad-hoc — 现有的 SPI ABC 是临时定义的:
- `EvaluatorABC` (plugin/spi/evaluator.py)
- `ChannelABC` (channel/adapter.py)
- `AuthProviderABC` (auth/provider.py)
- `SecretProviderABC` (vault/provider.py)
- `PreLLMHook / PostLLMHook / PreToolHook / PostToolHook` (engine/guardrail.py)
- `InferenceBackendABC` (model_hub/inference_manager.py)

These are scattered across the codebase without a unified type system. Third-party developers have no single entry point (`hecate.plugin` module) or template generator (`hecate plugin init`) to start building plugins.

这些分散在代码库中，没有统一的类型系统。第三方开发者没有单一的入口点（`hecate.plugin` 模块）或模板生成器（`hecate plugin init`）来开始构建插件。

**Research basis — 研究基础**: Analyzed 14 platforms (Dify, Claude Code, OpenClaw, Google ADK, AgentScope, deer-flow, HermesAgent, watsonx, Bedrock, Salesforce, Palantir AIP, AgentArts, Versatile, openjiuwen). Key findings — 关键发现:
- Tool Plugin appears in 14/14 platforms — 工具插件出现在 14/14 个平台
- Hook/Extension appears in 10/14 platforms — 钩子/扩展出现在 10/14 个平台
- Google ADK's `BasePlugin` (one class with optional callback methods) is the cleanest hook design — Google ADK 的 `BasePlugin`（一个类带有可选回调方法）是最简洁的钩子设计
- HermesAgent's "general plugin + specialized types" model maps well to Hecate — HermesAgent 的"通用插件 + 专用类型"模型很适合 Hecate

## Goals / Non-Goals — 目标/非目标

**Goals — 目标:**
- Define 8 plugin types classified by capability (4 new ABCs + 4 existing ABCs with plugin.yaml support)
- Create `hecate.plugin` SDK module with type-safe base classes
- Create `hecate plugin init` CLI template generator
- Hot-reload during development
- Full install-time API surface validation
- API-type plugin online creation UI

**Non-Goals — 非目标:**
- Datasource Plugin — deferred (overlaps with knowledge base system)
- AgentStrategy Plugin — deferred to P4 (touches Worker core architecture)
- Plugin packaging/distribution — that's 5.5b
- Plugin signing/security — that's 5.13 (P5)

## Decisions — 决策

### Decision 1: ExtensionPluginABC uses Google ADK BasePlugin pattern — ExtensionPluginABC 使用 Google ADK BasePlugin 模式

**Choice — 选择**: One class with optional callback methods (`on_pre_llm`, `on_post_llm`, `on_pre_tool`, `on_post_tool`), not 4 separate Hook ABCs.

**Rationale — 理由**: Google ADK's BasePlugin is the cleanest design — developers write one class, override only the methods they need. The existing PreLLMHook/PostLLMHook/PreToolHook/PostToolHook continue as internal engine interfaces; ExtensionPluginABC is the user-facing wrapper.

Google ADK 的 BasePlugin 是最简洁的设计 — 开发者编写一个类，只覆盖他们需要的方法。现有的 PreLLMHook/PostLLMHook/PreToolHook/PostToolHook 继续作为内部引擎接口；ExtensionPluginABC 是面向用户的包装器。

**Alternatives considered — 考虑的替代方案**:
- No new ABC, plugin.yaml declares which Hook interfaces it implements: Less type-safe, developers must understand 4 separate ABCs.
- Dynamic registration like OpenClaw/HermesAgent (`api.registerHook("pre_tool_call", callback)`): Too loose for Hecate's strict-type philosophy.

### Decision 2: Built-in providers stay code-registered, third-party uses plugin.yaml — 内置提供者保持代码注册，第三方使用 plugin.yaml

**Choice — 选择**: Existing `register_auth_providers()`, `register_channels()` etc. continue as-is. TP5 adds plugin.yaml support so third parties can load NEW instances of the same type.

**Rationale — 理由**: Chicken-and-egg — Auth Providers must be available before the plugin loading system starts (API requests need authentication). Also, built-in providers need access to internal config (e.g., `settings.JWT_SECRET`) that plugins shouldn't have. This matches AgentArts (预置 + 自定义), Salesforce (standard + custom actions), OpenClaw (bundled + installed).

先有鸡还是先有蛋 — 认证提供者必须在插件加载系统启动之前可用（API 请求需要认证）。此外，内置提供者需要访问插件不应拥有的内部配置（例如 `settings.JWT_SECRET`）。这与 AgentArts（预置 + 自定义）、Salesforce（标准 + 自定义操作）、OpenClaw（捆绑 + 安装）一致。

### Decision 3: TriggerPluginABC supports 3 trigger sources — TriggerPluginABC 支持 3 种触发源

**Choice — 选择**: `webhook` (HTTP POST → handler), `schedule` (cron expression → handler), `event` (internal event bus → handler). Based on Versatile's 4 trigger types (Webhook/Schedule/Event/MCP), minus MCP (handled by MCP Client 5.3).

### Decision 4: hecate.plugin SDK module structure — hecate.plugin SDK 模块结构

**Choice — 选择**: Single import path `from hecate.plugin import ToolPluginABC, ExtensionPluginABC, ...`. All 8 type ABCs re-exported from one module. SDK also provides `PluginContext` for config injection and `register()` helper.

### Decision 5: CLI uses click (already a dependency) — CLI 使用 click（已经是依赖）

**Choice — 选择**: `hecate plugin init <name> --type <type>` scaffolds plugin directory. Uses click for CLI parsing (check if already a dependency; if not, use argparse to avoid new dependency).

## Risks / Trade-offs — 风险/权衡

- **[ExtensionPluginABC parallel to existing Hooks — ExtensionPluginABC 与现有钩子并行]** → Two interfaces for the same concept. Mitigation: ExtensionPluginABC is a thin wrapper; internal engine continues using Hook ABCs directly. The bridge is in the loader.
  缓解措施：ExtensionPluginABC 是薄包装器；内部引擎继续直接使用 Hook ABC。桥接在加载器中。

- **[Trigger Plugin scope creep — 触发器插件范围蔓延]** → Event-driven triggers could grow complex (message queues, event sourcing). Mitigation: TP5 only does webhook + schedule + simple event bus. MQ integration is future work.
  缓解措施：TP5 仅实现 webhook + 调度 + 简单事件总线。MQ 集成是未来的工作。

- **[Hot-reload reliability — 热重载可靠性]** → File watching in async Python can be tricky. Mitigation: Use `watchdog` library (mature, cross-platform). Only enable in development mode (not production).
  缓解措施：使用 `watchdog` 库（成熟、跨平台）。仅在开发模式下启用（非生产环境）。

- **[API validation complexity — API 验证复杂性]** → Full API surface validation requires introspecting plugin classes at install time. Mitigation: Start with method signature checking (has expected methods with correct params). Deep type annotation checking is future work.
  缓解措施：从方法签名检查开始（具有正确参数的预期方法）。深层次类型注解检查是未来的工作。
