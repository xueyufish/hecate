## ADDED Requirements — 新增需求

### Requirement: MCP Server 和 Client 配置设置
`Settings` 类 SHALL 包含以下 MCP 相关设置：
- `MCP_SERVER_ENABLED: bool`（默认：`False`）— 启用/禁用 MCP Server
- `MCP_SERVER_HOST: str`（默认：`"0.0.0.0"`）— MCP Server 绑定主机
- `MCP_SERVER_PORT: int`（默认：`8000`）— MCP Server 绑定端口（挂载时提供信息）
- `MCP_AUTH_TYPE: str`（默认：`"api_key"`）— MCP 请求的认证模式（`api_key`、`jwt`、`none`）
- `MCP_TRANSPORT: str`（默认：`"http"`）— MCP Server 的传输模式（`http` 用于 Streamable HTTP）
- `MCP_CLIENT_TIMEOUT: int`（默认：`30`）— MCP Client 操作超时秒数

#### Scenario: 默认 MCP 配置
- **WHEN** 未设置 MCP 相关环境变量
- **THEN** `MCP_SERVER_ENABLED=False`、`MCP_AUTH_TYPE="api_key"`、`MCP_TRANSPORT="http"`、`MCP_CLIENT_TIMEOUT=30`

#### Scenario: 启用 MCP server
- **WHEN** `MCP_SERVER_ENABLED=true`
- **THEN** MCP Server ASGI 应用 SHALL 被挂载到 FastAPI 应用的 `/mcp` 路径

#### Scenario: 自定义认证类型
- **WHEN** `MCP_AUTH_TYPE=none`
- **THEN** MCP 工具调用 SHALL 跳过 API 密钥验证
