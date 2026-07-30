## Context — 背景

Hecate 的 Plugin SPI Core（5.5a，已归档）建立了注册层：`PluginRegistry`、`PluginManifest`（冻结 dataclass）和 `PluginLifecycle`（包含 `on_load`/`on_unload` 的 Protocol）。四个 SPI ABC 通过它注册：`EvaluatorABC`、`ChannelABC`、`AuthProviderABC`、`SecretProviderABC`。

然而，所有注册都是命令式的——硬编码的 Python 函数如 `register_auth_providers()` 创建实例并直接调用 `registry.register()`。没有声明式路径：没有 `plugin.yaml` 清单解析、没有目录扫描、没有配置注入、没有权限执行、没有数据库持久化、也没有管理 UI。

本次变更添加了**插件运行时引擎**，弥补了命令式注册与声明式插件加载、配置和管理之间的差距。

**竞品分析基础**：研究了 14 个平台（Dify、Claude Code、OpenClaw、Google ADK、AgentScope、deer-flow、watsonx、Bedrock AgentCore、Salesforce Agentforce、Palantir AIP、Huawei AgentArts、Huawei Versatile、openjiuwen、HermesAgent）。以下关键决策均基于此分析。

## Goals / Non-Goals — 目标 / 非目标

**目标：**
- 通过 `plugin.yaml` 清单实现的声明式插件加载
- 本地目录发现（启动时扫描 `plugins/` 目录）
- 基本的 `api_version` / `min_platform_version` 兼容性验证
- 扩展的生命周期钩子：`on_enable`、`on_disable`、`on_config_change`
- 数据库支持的插件状态管理，支持按工作空间启用（双层作用域：平台 + 工作空间）
- 配置管理：`config_schema`（JSON Schema）→ 数据库 → 运行时注入
- 清单中的权限声明 + 运行时执行
- 入口加载：`python:module:Class`（进程内 importlib）+ `mcp://endpoint`（通过 MCP Client 5.3）
- 插件管理的 REST API（列表、启用、禁用、配置）
- 前端插件管理页面，带自动生成的配置表单

**非目标（延期）：**
- 6 种插件类型 ABC（Tool/Trigger/Extension/Model/Datasource/AgentStrategy）→ TP5
- `hecate.plugin` Python SDK 模块 → TP5
- `hecate plugin init` CLI 模板生成器 → TP5
- 开发期间的热重载 → TP5
- 完整的 `pluginApi` 安装时 API 面验证 → TP5
- API 类型插件的在线创建 UI → TP5
- 插件打包格式（`.hecate-plugin` 包）→ 5.5b
- 打包 CLI、上传/安装/卸载 UI → 5.5b
- 版本管理 + 升级工作流 → 5.5b
- 插件签名和安全扫描 → 5.13（P5）
- 进程外守护进程 / WASM 隔离 → P5
- 市场分发 → P5（12.0）

## Decisions — 决策

### Decision 1: 进程内 + MCP 混合方案（无自定义守护进程）

**选择**：插件通过 `importlib` 在进程内加载。远程插件通过现有 MCP Client 连接。无自定义插件守护进程。

**理由**：研究了 14 个平台。行业趋势是 MCP 取代自定义守护进程协议（Bedrock AgentCore 和 Huawei Versatile 已采用 MCP 优先）。Dify 的守护进程是 2024 年在 MCP 成熟之前的解决方案。OpenClaw 证明了多租户 + 进程内方案在合适的清单契约下是可行的。

**考虑的替代方案**：
- Dify 风格的守护进程（子进程 + stdio/TCP IPC）：已拒绝——工作量翻倍（IPC 协议 + SPI 代理层 + 守护进程管理器），而且 MCP 已经处理了"远程插件"场景。
- Bedrock 风格的纯 MCP：已拒绝——对简单本地操作（例如每次请求的 JWT 认证验证）强制使用 JSON-RPC 开销。
- 混合守护进程 + 进程内：已拒绝——维护两条执行路径增加了复杂性，而在 MCP 覆盖远程访问的情况下没有明显收益。

### Decision 2: 数据库作为运行时真理源

**选择**：`PluginModel` 数据库表存储插件状态（状态、配置、workspace_id）。`plugin.yaml` 是开发时声明；在安装/注册时，清单数据写入数据库。运行时仅从数据库读取。

**理由**：所有带 Web UI 的企业平台（AgentArts、Versatile、Salesforce、Palantir）都使用数据库作为单一真理源。基于文件的配置（OpenClaw）仅适用于没有 Web 管理的 CLI 驱动工具。

**简化 Schema**：
```
PluginModel:
  id: UUID（主键）
  name: str（按工作空间唯一）
  type: str
  version: str
  status: enum(installed, enabled, disabled, error)
  entry: str  # "python:module:Class" 或 "mcp://endpoint"
  manifest: JSON  # 完整的 plugin.yaml 内容
  config: JSON  # 运行时配置值（根据 config_schema 验证）
  workspace_id: UUID | None  # None = 平台级插件
  created_at, updated_at, deleted_at
```

### Decision 3: 双层作用域（平台 + 工作空间）

**选择**：平台级插件（随 Hecate 发布，`workspace_id=None`）全局可用。工作空间级插件按工作空间安装。

**理由**：AgentArts、Versatile、Salesforce 都使用这种双层模型——官方预装插件（全局）+ 用户创建插件（按工作空间/组织）。

### Decision 4: 权限声明 + 执行（Dify 模型）

**选择**：插件在 `plugin.yaml` 中声明权限（`permissions: [network:https, filesystem:read]`）。加载器在注册时验证。运行时强制执行——未声明的权限被拒绝。

**理由**：Dify 的研究发现沙箱化损害了插件开发体验（依赖限制）。签名 + 权限声明是务实的替代方案。深度签名/扫描延期至 5.13（P5）。

### Decision 5: config_schema → UI 自动生成

**选择**：`plugin.yaml` 中的 `config_schema` 使用 JSON Schema。后端在保存时根据 schema 验证配置。前端根据 schema 自动生成表单字段（string→文本输入，secret string→密码，enum→下拉框，带 min/max 的 number→带边界的数字输入）。

**理由**：AgentArts 使用由 schema 定义驱动的输入/输出参数表单。Salesforce Apex 动作从 InvocableVariable 描述自动生成参数表单。这是企业平台的标准模式。

### Decision 6: 通过可选的协议方法扩展 PluginLifecycle

**选择**：向 `PluginLifecycle` Protocol 添加 `on_enable`、`on_disable`、`on_config_change`。未实现这些方法的插件不受影响（Protocol 结构类型化）。

```python
class PluginLifecycle(Protocol):
    def on_load(self) -> None: ...
    def on_unload(self) -> None: ...
    # 新的可选钩子
    def on_enable(self) -> None: ...
    def on_disable(self) -> None: ...
    def on_config_change(self, new_config: dict[str, Any]) -> None: ...
```

**理由**：支持无需完全重载的运行时启用/禁用，以及无需重新注册的配置热更新。Protocol 上的 `@runtime_checkable` 允许加载器通过 `hasattr` 检测插件实现了哪些钩子。

## Risks / Trade-offs — 风险 / 权衡

- **[进程内崩溃传播]** → 插件异常可能导致主进程崩溃。缓解措施：加载器将 `on_load`/`on_enable` 包裹在 try/except 中，记录错误，在数据库中将插件状态标记为 `error`。消费者代码（PluginRegistry 调用者）已经为现有 SPI 将插件调用包裹在 try/except 中。

- **[插件间的依赖冲突]** → 两个插件需要同一包的不同版本。缓解措施：文档化要求插件应锁定兼容的版本。每个插件独立虚拟环境是未来的增强功能（非 5.5）。目前，插件开发者必须确保兼容性。

- **[权限执行粒度]** → 权限声明是粗粒度的（`network:https`、`filesystem:read`）。不是基于能力的安全模型。缓解措施：这与 Dify 的务实模型一致。细粒度的基于能力的安全（WASM）是 P5。

- **[插件发现性能]** → 每次启动扫描 `plugins/` 目录，如果插件数量多可能会慢。缓解措施：将发现的清单缓存到数据库；仅在目录 mtime 变化时重新扫描。

- **[配置 schema 验证开销]** → 每次配置保存时进行 JSON Schema 验证。缓解措施：`jsonschema` 已经是依赖项（Graph DSL）。配置保存不频繁（管理操作），不是热路径。
