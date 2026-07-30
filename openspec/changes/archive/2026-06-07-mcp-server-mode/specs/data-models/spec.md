## ADDED Requirements — 新增需求

### Requirement: ToolModel 支持 MCP 服务器连接元数据
`ToolModel` SHALL 包含 `mcp_server`（字符串，可空）和 `mcp_tool_name`（字符串，可空）字段，用于标识 `source="mcp"` 工具的来源 MCP 服务器和工具名称。这些字段已存在于当前 schema 中；无需迁移。

#### Scenario: 带服务器引用的 MCP 工具
- **WHEN** 使用 `source="mcp"` 创建了一个工具，并带有 `mcp_server="my-remote-server"` 和 `mcp_tool_name="search"`
- **THEN** ToolRegistry SHALL 使用这些字段将执行路由到正确的 MCP 客户端连接
