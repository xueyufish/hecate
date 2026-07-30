## MODIFIED Requirements — 修改的需求

### Requirement: ToolRegistry 按源类型路由工具执行 — ToolRegistry 按源类型路由工具执行
系统 SHALL 在 `services/tool/registry.py` 中提供一个 `ToolRegistry` 服务，接受工具名称、参数和可选上下文，按源类型查找工具定义，并将执行路由到适当的执行器。

#### Scenario: 内置工具执行
- **WHEN** 调用 `registry.execute("web_search", {"query": "test"}, context)` 且名为 "web_search" 的内置工具存在
- **THEN** registry SHALL 路由到 `BuiltInToolExecutor` 并返回工具的结果

#### Scenario: 自定义工具执行（尚未实现）
- **WHEN** 调用 `registry.execute("my_tool", args, context)` 且工具的 `source="custom"`
- **THEN** registry SHALL 抛出 `NotImplementedError`，消息指示自定义工具执行尚不可用

#### Scenario: 通过 MCP Client 的 MCP 工具执行
- **WHEN** 调用 `registry.execute("mcp_tool", args, context)` 且工具的 `source="mcp"` 并有非空的 `mcp_server` 字段
- **THEN** registry SHALL 通过 `MCPClientManager.call_tool(server_name=tool.mcp_server, tool_name=tool.mcp_tool_name, arguments=args)` 路由调用并返回结果

#### Scenario: 没有已连接服务器的 MCP 工具
- **WHEN** 调用 `registry.execute("mcp_tool", args, context)` 且工具的 `mcp_server` 值在 `MCPClientManager` 中没有活动连接
- **THEN** registry SHALL 抛出 `ConnectionError`，消息指示 MCP 服务器未连接

#### Scenario: 未知工具名称
- **WHEN** 调用 `registry.execute("nonexistent", args, context)` 且没有具有该名称的工具
- **THEN** registry SHALL 抛出 `ValueError`，消息指示未找到该工具
