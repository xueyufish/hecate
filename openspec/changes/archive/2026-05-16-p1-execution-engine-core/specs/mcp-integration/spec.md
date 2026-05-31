## ADDED Requirements

### Requirement: MCP Client 连接管理

系统 MUST 实现 MCP Client，能够连接到一个或多个 MCP Server。MCP Server 的连接地址 SHALL 从配置文件加载，支持 stdio 和 SSE 两种传输协议。系统 MUST 在启动时尝试连接所有已配置的 MCP Server，连接失败时 MUST 记录警告日志但不阻止系统启动。系统 SHALL 维护每个 MCP Server 的连接状态（connected | disconnected）。

#### Scenario: 启动时连接多个 MCP Server
- **WHEN** 配置文件中定义了 3 个 MCP Server（A、B、C），其中 B 地址不可达
- **THEN** 系统 MUST 成功连接 A 和 C（状态为 connected），B 连接失败记录警告日志（状态为 disconnected），系统正常启动

#### Scenario: 运行时 MCP Server 断线重连
- **WHEN** 已连接的 MCP Server A 在运行过程中断开连接
- **THEN** 系统 MUST 将 A 的状态更新为 disconnected，后续工具调用返回连接错误，系统 SHALL 周期性尝试重连

### Requirement: MCP Tool 发现

系统 MUST 能够从已连接的 MCP Server 发现其提供的工具列表。连接成功后，系统 SHALL 调用 MCP Server 的 `tools/list` 方法获取工具列表。发现的工具 MUST 自动注册到 Hecate 的 tools 表中，source 设为 `"mcp"`，`mcp_server` 记录来源 Server 标识，`mcp_tool_name` 记录 MCP 侧的工具名。工具参数 Schema MUST 从 MCP Server 返回的 `inputSchema` 中提取。

#### Scenario: 发现并注册 MCP Server 的工具
- **WHEN** MCP Server A 提供了 `web_search` 和 `calculator` 两个工具
- **THEN** 系统 MUST 调用 A 的 `tools/list`，在 Hecate tools 表中注册两条记录（source="mcp"，mcp_server="A"），parameters 从 MCP 的 inputSchema 转换

#### Scenario: MCP Server 工具列表变更同步
- **WHEN** MCP Server A 新增了 `weather_query` 工具，系统定期刷新工具列表
- **THEN** 系统 MUST 在 tools 表中新增一条记录，已有的工具记录保持不变

### Requirement: MCP Tool 调用

系统 MUST 支持通过 MCP 协议调用工具。当执行引擎遇到 source="mcp" 的工具时，MUST 通过 MCP Client 向对应的 MCP Server 发送 `tools/call` 请求。调用参数 MUST 在发送前根据工具的 parameters Schema 进行校验。MCP Server 返回结果后，系统 MUST 将结果封装为 `WorkerResult.output` 格式返回给执行引擎。

#### Scenario: 成功调用 MCP 工具
- **WHEN** 执行引擎调用 "web_search" 工具（source="mcp"，mcp_server="A"），参数为 `{"query": "Hecate"}`
- **THEN** 系统 MUST 向 MCP Server A 发送 `tools/call` 请求（name="web_search", arguments={"query": "Hecate"}），将返回结果封装后传递给引擎

#### Scenario: 工具调用参数校验失败
- **WHEN** 调用 "calculator" 工具时缺少必需参数 "expression"
- **THEN** 系统 MUST 在发送 MCP 请求之前拒绝调用，返回参数校验错误

#### Scenario: MCP Server 调用超时
- **WHEN** MCP Server 在配置的超时时间内（默认 30 秒）未返回结果
- **THEN** 系统 MUST 返回 `WorkerResult(status="timeout")`，记录超时错误日志

### Requirement: P1 仅 Client 模式

P1 阶段系统 MUST 仅实现 MCP Client 功能，不实现 MCP Server。系统 SHALL NOT 向外暴露 MCP Server 接口。所有 MCP 交互 MUST 由 Hecate 作为 Client 发起（连接、发现、调用）。MCP Server 的实现 SHALL 作为 P2 扩展点预留。

#### Scenario: P1 不暴露 MCP Server 端点
- **WHEN** 外部系统尝试通过 MCP 协议连接 Hecate
- **THEN** Hecate MUST NOT 响应 MCP Server 协议请求

### Requirement: MCP 工具在 Agent 中可用

通过 MCP 发现并注册的工具 MUST 可被 Agent 绑定使用。Agent 的 `tools` 配置中 MUST 能引用 MCP 工具（通过工具名或 ID）。执行引擎调用 MCP 工具时，MUST 与调用内置工具/自定义工具走相同的 `EnginePort.tool_execute()` 接口，工具调用对引擎透明。

#### Scenario: Agent 绑定并使用 MCP 工具
- **WHEN** Agent 配置了 tools 包含 "web_search"（MCP 工具），用户对话触发 LLM 返回 tool_call 调用 "web_search"
- **THEN** 执行引擎 MUST 通过 EnginePort.tool_execute("web_search", args) 调用，模型路由层识别为 MCP 工具并通过 MCP Client 执行
