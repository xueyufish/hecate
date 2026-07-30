## ADDED Requirements — 新增需求

### Requirement: MCP Client 连接管理 — MCP Client Connection Management

系统 MUST 实现 MCP Client，支持 stdio 和 SSE 两种传输协议。启动时尝试连接所有配置的 MCP Server，失败时记录警告但不阻止启动。维护每个 Server 的连接状态（connected/disconnected）。
— System MUST implement MCP Client supporting stdio and SSE transports. Log warnings on connection failures without blocking startup.

#### Scenario: 启动时连接多个 MCP Server — Connect multiple MCP Servers on startup
- **WHEN** 配置了 3 个 MCP Server（A、B、C），B 地址不可达
- **THEN** 系统 MUST 成功连接 A 和 C，B 记录警告日志

#### Scenario: 运行时 MCP Server 断线重连 — Reconnect on runtime disconnection
- **WHEN** 已连接的 Server A 运行中断开
- **THEN** 系统 MUST 更新状态为 disconnected，周期性尝试重连

### Requirement: MCP Tool 发现 — MCP Tool Discovery

系统 MUST 从已连接的 MCP Server 发现工具列表。调用 `tools/list` 获取工具，自动注册到 Hecate tools 表（source="mcp"）。
— System MUST discover tools from connected MCP Servers via `tools/list` and register in tools table.

#### Scenario: 发现并注册 MCP Server 的工具 — Discover and register MCP tools
- **WHEN** MCP Server A 提供 `web_search` 和 `calculator` 两个工具
- **THEN** 系统 MUST 注册两条记录（source="mcp"）

#### Scenario: MCP Server 工具列表变更同步 — Sync tool list changes
- **WHEN** Server A 新增 `weather_query` 工具
- **THEN** 系统 MUST 新增记录，已有记录保持不变

### Requirement: MCP Tool 调用 — MCP Tool Invocation

系统 MUST 支持通过 MCP 协议调用工具。执行引擎遇到 source="mcp" 的工具时，向对应 Server 发送 `tools/call` 请求。
— System MUST invoke tools via MCP protocol using `tools/call`.

#### Scenario: MCP Server 调用超时 — MCP Server call timeout
- **WHEN** Server 在 30 秒内未返回结果
- **THEN** 系统 MUST 返回 `WorkerResult(status="timeout")`

### Requirement: P1 仅 Client 模式 — P1 Client-Only Mode

P1 MUST implement MCP Client only, NOT MCP Server. MCP Server is P2 extension point.

#### Scenario: P1 不暴露 MCP Server 端点 — P1 doesn't expose MCP Server
- **WHEN** 外部系统尝试通过 MCP 协议连接 Hecate
- **THEN** Hecate MUST NOT 响应

### Requirement: MCP 工具在 Agent 中可用 — MCP Tools Available in Agents

MCP tools MUST be bindable to Agents via `tools` config. Engine calls them through same `EnginePort.tool_execute()` interface transparently.

#### Scenario: Agent 绑定并使用 MCP 工具 — Agent binds and uses MCP tool
- **WHEN** Agent 配置了 MCP 工具 "web_search"，LLM 触发调用
- **THEN** 引擎通过 `EnginePort.tool_execute()` 透明调用
