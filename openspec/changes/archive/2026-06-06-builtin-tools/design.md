## Context — 上下文

两个存根实现目前阻止了所有工具执行：

1. `_ProductionEnginePort.tool_execute()`（engine_port_adapter.py 第 66 行）返回 `f"Executed {name} with args {args}"`——一个 mock 字符串。
2. `ConversationService._execute_tools_with_evidence()`（conversation.py 第 674 行）返回相同的 mock 字符串。

引擎层有一个完整的工具执行管道：`ToolWorker` 从消息中提取工具调用，调用 PreToolHook/PostToolHook 防护栏，调用 `EnginePort.tool_execute()`，并返回工具结果消息。服务层有 `tool_calling.py` 用于 OpenAI 函数格式转换、`ToolFilter` 用于基于阶段的过滤、以及 `MCPClient` 用于外部工具发现。数据层有 `ToolModel`，其 `source` 字段支持 "builtin"、"custom"、"mcp"。

缺少的是**执行层**：一个将工具名称映射到可执行函数的注册表，并按源类型路由。统一执行引擎设计（已归档的变更 `2026-06-05-unified-execution-engine`）将工具调用定义为循环图模式（`ConversationNode → ConditionNode → ToolNode → ConversationNode`），由 `_ToolWorker` 通过 EnginePort 处理执行。本变更将真实的执行插入到该架构中。

可以利用的现有基础设施：
- `SandboxExecutor` + `SandboxPool`（services/sandbox/）——基于 Docker 的沙箱执行，带资源限制
- `ToolModel`（models/tool.py）——ORM，包含 `source`、`parameters`（JSON Schema）、`sandbox_enabled`、`risk_level`
- `ToolCreateSchema` / `ToolReadSchema`——API 模式已支持 source="builtin"
- 记忆工具模式（conversation.py 中的 `_build_memory_tools()`）——演示了工具模式和执行在一个地方

## Goals / Non-Goals — 目标/非目标

**目标：**
- 实现 ToolRegistry 服务，按源类型路由 `tool_execute()` 调用
- 实现 5 个内置工具：web_search、read_file、write_file、list_files、execute_code
- 将 ToolRegistry 接入 `_ProductionEnginePort`，替换 mock 存根
- 在启动时将内置工具定义种子到数据库（混合注册模式）
- 通过环境变量支持可配置的搜索提供商（Tavily / Serper / DuckDuckGo）
- ToolRegistry 接口支持所有三种源类型（builtin/custom/mcp）

**非目标：**
- 自定义工具执行（source="custom"）——P2
- MCP 工具执行路由（source="mcp"）——P2（MCPClient 存在但路由未接入）
- 通过 API 创建工具的 POST /api/tools 端点——单独的变更
- MCP 同步服务（将发现的 MCP 工具持久化到数据库）——单独的变更
- 修改 `ConversationService._execute_tools_with_evidence()`——将被统一引擎迁移替代
- 沙箱化文件操作（read_file/write_file/list_files 在 P1 中以非沙箱方式运行；沙箱仅用于 execute_code）
- 工具版本控制或工具市场——P4

## Decisions — 决策

### D1：混合注册——代码定义模式，启动时数据库播种

**选择**：内置工具模式（名称、描述、参数 JSON Schema）在 Python 代码中定义（`services/tool/builtin.py`）。在应用程序启动时，一个种子函数将这些定义同步到 `tools` 数据库表（`source="builtin"`、`workspace_id=00000000`）。

**考虑的替代方案**：
- 仅代码（无数据库）→ 被拒绝：工具无法通过 API 可见，前端无法显示
- 仅数据库（迁移种子）→ 被拒绝：模式和执行逻辑分离，更难维护
- 完全不注册 → 被拒绝：`GET /api/tools?source=builtin` 必须返回内容

**理由**：代码定义模式将定义与执行放在一起（单一模块维护）。数据库播种使工具可通过 API 查询并在 UI 中可见。启动时同步确保数据库始终与代码匹配——如果在代码中添加了一个工具，它会自动出现在数据库中。

### D2：ToolRegistry 是服务层单例，而非引擎内部

**选择**：`ToolRegistry` 位于 `services/tool/registry.py`。它接受一个数据库会话，通过名称 + workspace_id 查找 `ToolModel` 以确定源类型，然后路由到相应的执行器。

**考虑的替代方案**：
- 引擎内部（engine/registry.py）→ 被拒绝：引擎零外部依赖；ToolRegistry 需要数据库访问
- 每次请求实例化 → 被拒绝：不必要的开销；注册表除了执行器引用外是无状态的

**理由**：ToolRegistry 需要数据库访问（查找工具定义）和服务引用（SandboxExecutor、MCPClient）。两者都是服务层关注点。引擎保持解耦——它调用 `EnginePort.tool_execute()`，后者委派给注册表。

### D3：具有可插拔适配器的搜索提供商抽象

**选择**：`SearchProvider` ABC，具有 Tavily、Serper、DuckDuckGo 的实现。通过 `SEARCH_PROVIDER` 和 `SEARCH_API_KEY` 环境变量进行配置。`BuiltInToolExecutor` 在构造时解析提供商。

**考虑的替代方案**：
- 单个硬编码提供商（Tavily）→ 被拒绝：将用户锁定在一个供应商
- 每次请求选择提供商 → 被拒绝：P1 过度设计

**理由**：三个提供商具有不同的 API 形状但相同的输入/输出契约（query → results）。ABC 模式匹配现有的 EnginePort 抽象。环境变量配置匹配现有的 `core/config.py` 模式（pydantic-settings）。

### D4：execute_code 委托给现有的 SandboxExecutor

**选择**：`execute_code` 内置工具使用用户的 Python 代码调用 `SandboxExecutor.execute()`，返回 stdout/stderr。使用现有的 `SandboxConfig` 默认值（128MB 内存、50% CPU、30 秒超时、无网络）。

**考虑的替代方案**：
- 直接子进程 → 被拒绝：无安全隔离
- E2B 云端沙箱 → 被拒绝：外部依赖，Docker 沙箱已实现

**理由**：`SandboxExecutor` + `SandboxPool` 已经实现（services/sandbox/）。`ToolModel.sandbox_enabled` 字段已存在。这只是将它们连接起来。

### D5：custom 和 mcp 路由路径抛出 NotImplementedError

**选择**：ToolRegistry 的路由逻辑有三个分支。仅实现了 `builtin`。`custom` 和 `mcp` 路径抛出带有描述性消息的 `NotImplementedError`。

**理由**：显式失败优于静默存根（当前行为）。接口正确；实现在 P2 中跟进。调用者得到清晰的错误而不是假字符串。

### D6：文件操作工具在可配置的工作区目录内工作

**选择**：`read_file`、`write_file`、`list_files` 相对于可配置的 `WORKSPACE_ROOT` 目录操作（默认：`./workspace`）。路径经过净化以防止目录遍历。

**理由**：Agent 需要文件访问但必须受到限制。工作区根目录防止任意文件系统访问。路径净化（`os.path.normpath` + 前缀检查）是最小可行安全措施。

## Risks / Trade-offs — 风险/权衡

| 风险 | 缓解措施 |
|------|---------|
| web_search 需要搜索 API 密钥 | DuckDuckGo 提供商无需 API 密钥（默认回退）；Tavily/Serper 需要密钥但有免费层级 |
| 并非所有环境都可用 Docker | `execute_code` 优雅降级：如果 Docker 守护进程不可用，返回错误消息而非崩溃 |
| 启动时种子可能与现有数据冲突 | 更新插入模式：在插入前按名称 + source="builtin" 检查；如果定义已变更则更新模式 |
| 文件工具存在路径遍历风险 | 对所有路径进行 WORKSPACE_ROOT 净化；拒绝 `..` 组件和绝对路径 |
| ToolRegistry 为简单工具调用增加延迟 | 内置工具名称到执行器函数的内存字典；仅当名称不在内置集合中时才进行数据库查找 |
