## Why — 为什么

Hecate's Plugin System (5.5 ✅) provides the runtime engine for loading plugins via `plugin.yaml`, but it has no type taxonomy — there are no ABCs defining what a "Tool Plugin" or "Extension Plugin" should implement. The existing SPI ABCs (EvaluatorABC, ChannelABC, AuthProviderABC, SecretProviderABC) were defined ad-hoc in Sprint 4/5 without a unified type system. Third-party developers have no type-safe contracts to build against, and the `hecate.plugin` SDK module doesn't exist. This blocks the developer ecosystem — without defined plugin types, no one can write a plugin.

Hecate 的插件系统（5.5 ✅）提供通过 `plugin.yaml` 加载插件的运行时引擎，但没有类型分类法 — 没有定义"工具插件"或"扩展插件"应实现的 ABC。现有的 SPI ABC（EvaluatorABC、ChannelABC、AuthProviderABC、SecretProviderABC）是在 Sprint 4/5 中临时定义的，没有统一的类型系统。第三方开发者没有类型安全的契约可供开发，`hecate.plugin` SDK 模块也不存在。这阻碍了开发者生态系统 — 没有定义的插件类型，没人能编写插件。

## What Changes — 变更内容

- **4 new plugin type ABCs**: `ToolPluginABC` (callable function tools), `ExtensionPluginABC` (Guardrail Hook injection — Google ADK BasePlugin pattern with optional `on_pre_llm` / `on_post_llm` / `on_pre_tool` / `on_post_tool` methods), `TriggerPluginABC` (event-driven: webhook/schedule/MQ), `ModelPluginABC` (custom LLM provider based on existing InferenceBackendABC)
- **4 existing ABCs gain plugin.yaml support**: ChannelABC, EvaluatorABC, AuthProviderABC, SecretProviderABC — third parties can now load these via plugin.yaml alongside the existing code-registered built-in providers
- **`hecate.plugin` SDK module**: Type-safe base classes, registration helpers, config injection utilities, permission checking — the developer-facing API for writing plugins
- **`hecate plugin init` CLI**: Template generator that scaffolds a new plugin project (plugin.yaml + Python module + tests skeleton) for any of the 8 types
- **Hot-reload**: File watcher detects plugin.yaml changes during development, re-registers plugins without restart
- **Full `pluginApi` install-time validation**: API surface compatibility checks (SDK version + method signature verification) beyond 5.5's basic version string check
- **API-type plugin online creation UI**: AgentArts-style form-driven UI for creating simple plugins (especially Tool and Trigger types) without writing code
- **Deferred**: Datasource Plugin (overlaps with knowledge base), AgentStrategy Plugin (touches Worker core, P4)

- **4 个新的插件类型 ABC**：`ToolPluginABC`（可调用函数工具）、`ExtensionPluginABC`（Guardrail Hook 注入 — Google ADK BasePlugin 模式，包含可选的 `on_pre_llm`/`on_post_llm`/`on_pre_tool`/`on_post_tool` 方法）、`TriggerPluginABC`（事件驱动：webhook/调度/MQ）、`ModelPluginABC`（基于现有 InferenceBackendABC 的自定义 LLM 提供者）
- **4 个现有 ABC 获得 plugin.yaml 支持**：ChannelABC、EvaluatorABC、AuthProviderABC、SecretProviderABC — 第三方现在可以通过 plugin.yaml 加载这些，与现有的代码注册内置提供者并存
- **`hecate.plugin` SDK 模块**：类型安全基类、注册辅助工具、配置注入工具、权限检查 — 面向开发者的插件编写 API
- **`hecate plugin init` CLI**：模板生成器，为 8 种类型中的任意一种生成新的插件项目脚手架（plugin.yaml + Python 模块 + 测试骨架）
- **热重载**：文件监视器在开发期间检测 plugin.yaml 变化，无需重启即可重新注册插件
- **完整的 `pluginApi` 安装时验证**：超越 5.5 基本版本字符串检查的 API 表面兼容性检查（SDK 版本 + 方法签名验证）
- **API 类型插件在线创建 UI**：AgentArts 风格的表单驱动 UI，无需编写代码即可创建简单插件（尤其是 Tool 和 Trigger 类型）
- **推迟**：数据源插件（与知识库重叠）、AgentStrategy 插件（涉及 Worker 核心，P4）

## Capabilities — 能力

### New Capabilities — 新能力

- `plugin-type-taxonomy`: 8 plugin type ABCs (4 new + 4 existing with plugin.yaml support), hecate.plugin SDK module, hecate plugin init CLI, hot-reload, install-time API validation, API-type plugin creation UI

### Modified Capabilities — 修改的能力

- `plugin-system`: Plugin loader (from 5.5) gains awareness of plugin types — validates that loaded plugins implement the correct ABC for their declared type

## Impact — 影响

- **New files — 新文件**:
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
- **Modified files — 修改的文件**:
  - `src/hecate/plugin/loader.py` — type-aware loading (validate ABC match)
  - `src/hecate/plugin/spi/__init__.py` — re-export existing ABCs through unified type system
  - `src/hecate/main.py` — register `hecate plugin` CLI subcommand
  - `web/src/app/(dashboard)/plugins/page.tsx` — add "Create Plugin" button
- **Dependencies — 依赖**: `watchdog` (file watcher for hot-reload), `click` or `typer` (CLI — check existing)
