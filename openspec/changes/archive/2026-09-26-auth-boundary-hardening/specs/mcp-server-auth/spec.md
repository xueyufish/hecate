# mcp-server-auth Delta

## Purpose

定义 MCP Server（`/mcp` Streamable HTTP 端点）的传输层认证与工具执行的身份语义：认证在传输层强制，工具以服务端解析的身份执行，调用方参数不作为授权依据。

## ADDED Requirements

### Requirement: MCP 传输层强制认证

MCP Server 挂载于 `/mcp` 时 SHALL 在传输层强制认证：每个请求的 Bearer 凭据经与 REST API 相同的统一认证链（JWT → 数据库 API key → env 引导 key）解析；认证失败 SHALL 返回 401。`MCP_AUTH_TYPE=none` SHALL 仅为显式配置的开发逃生口，启用时 SHALL 输出警告日志；默认配置 SHALL 强制认证。

#### Scenario: 匿名 MCP 请求被拒

- **WHEN** 未携带凭据的请求访问 `/mcp` 的任意操作（如 `tools/list`、`tools/call`）
- **THEN** 返回 401，不进入任何工具执行

#### Scenario: 有效 JWT 通过 MCP 传输认证

- **WHEN** 携带有效 JWT（含 workspace claim、成员资格有效）的 MCP 客户端调用 `tools/list`
- **THEN** 认证通过，服务端建立该用户的认证上下文

#### Scenario: 显式关闭认证时告警

- **WHEN** 部署显式设置 `MCP_AUTH_TYPE=none` 并启动 MCP Server
- **THEN** 服务启动输出警告日志，请求不做传输层认证（仅限开发环境）

### Requirement: 工具以服务端身份执行

MCP 工具 SHALL 以服务端解析的认证身份执行：身份来自传输层建立的认证上下文，而非工具参数。涉及租户资源的工具（agent/knowledge/tool/session/conversation 的列举与操作）SHALL 按该身份的 workspace 过滤；调用方提供的 workspace 标识参数 SHALL NOT 作为授权依据（至多作为冗余输入，与服务端身份冲突时以服务端为准）。无认证上下文的工具调用（如绕过传输层的进程内调用）SHALL 返回错误而非以全局权限执行。

#### Scenario: 调用方伪造 workspace 参数被服务端覆盖

- **WHEN** workspace A 的用户调用 `agent_list` 并传入 workspace B 的 ID 作为参数
- **THEN** 返回结果仅包含 workspace A 的 agent（服务端身份过滤生效）

#### Scenario: 跨租户 agent 对话被拒

- **WHEN** workspace A 的用户通过 MCP `agent_chat`/`session_create` 操作 workspace B 的 agent
- **THEN** 返回资源不存在错误，不执行对话

#### Scenario: 知识库与工具目录按租户过滤

- **WHEN** workspace A 的用户调用 `knowledge_list`/`tool_list`/`knowledge_search`
- **THEN** 返回结果仅包含调用者 workspace 可见的资源
