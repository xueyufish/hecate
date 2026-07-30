## ADDED Requirements — 新增需求

### Requirement: 使用官方 SDK 的真实 MCP Client
系统 SHALL 提供一个使用官方 `mcp` Python SDK（`modelcontextprotocol/python-sdk`）的生产级 MCP Client，支持 Streamable HTTP 和 stdio 传输以连接到外部 MCP 服务器。

#### Scenario: 通过 Streamable HTTP 连接到远程 MCP 服务器
- **WHEN** 调用 `HecateMCPClient.connect(server_url="http://remote-server:8000/mcp", transport="http")`
- **THEN** 客户端使用 `streamable_http_client` 建立 `ClientSession`，可以列出和调用工具

#### Scenario: 通过 stdio 连接到本地 MCP 服务器
- **WHEN** 调用 `HecateMCPClient.connect(command="python", args=["server.py"], transport="stdio")`
- **THEN** 客户端启动子进程并使用 `stdio_client` 建立 `ClientSession`

#### Scenario: 列出已连接服务器上的工具
- **WHEN** 成功连接后调用 `client.list_tools()`
- **THEN** 客户端返回工具定义列表，包含名称、描述和 inputSchema

#### Scenario: 在已连接服务器上调用工具
- **WHEN** 调用 `client.call_tool(tool_name="search", arguments={"query": "test"})`
- **THEN** 客户端向 MCP 服务器发送 `tools/call` 请求并返回结果

#### Scenario: 断开与服务器的连接
- **WHEN** 调用 `client.disconnect()`
- **THEN** 客户端关闭会话并清理资源

### Requirement: 用于多个服务器的 MCP Client 管理器
系统 SHALL 提供 `MCPClientManager`，同时管理到多个 MCP 服务器的连接，支持跨所有已连接服务器的工具发现和执行。

#### Scenario: 添加并连接到服务器
- **WHEN** 调用 `manager.add_server("my-server", server_url="http://localhost:8000/mcp")`
- **THEN** 管理器创建 `HecateMCPClient`、连接，并将其存储在 `"my-server"` 键下

#### Scenario: 从所有服务器发现工具
- **WHEN** 调用 `manager.discover_tools()`
- **THEN** 管理器聚合来自所有已连接服务器的工具，每个工具标记其来源服务器名称

#### Scenario: 在特定服务器上调用工具
- **WHEN** 调用 `manager.call_tool(server_name="my-server", tool_name="search", arguments={"q": "test"})`
- **THEN** 管理器将调用路由到指定服务器并返回结果

### Requirement: MCP Client 连接配置
系统 SHALL 提供 `MCP_CLIENT_TIMEOUT: int`（默认：`30`）设置，用于客户端连接和工具调用超时。

#### Scenario: 慢服务器超时
- **WHEN** MCP 服务器在 `MCP_CLIENT_TIMEOUT` 秒内没有响应
- **THEN** 客户端引发 `TimeoutError`，调用工具收到错误响应
