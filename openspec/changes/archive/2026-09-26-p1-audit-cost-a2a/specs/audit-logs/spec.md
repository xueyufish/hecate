# audit-logs Delta

## MODIFIED Requirements

### Requirement: AuditMiddleware for automatic API capture

The system SHALL register a FastAPI `BaseHTTPMiddleware` that captures every API request (excluding `/health`, `/metrics`, OPTIONS requests, and static assets). The middleware SHALL extract `AuthContext` for `user_id`, `org_id`, `workspace_id`, record request method, path, response status, and enqueue the audit event asynchronously. 认证依赖（REST 与 MCP 传输层）SHALL 在认证成功后统一把已解析的 `AuthContext` 写入请求状态（`request.state.auth_context`），使中间件读取到真实操作者而非匿名哨兵值。认证失败（401）SHALL 被审计为含失败类型（如 `invalid_credentials`、`malformed_credentials`）的事件，且 SHALL NOT 包含原始凭据（token、API key、密码）。MCP 传输层与 A2A 服务端的认证 SHALL 映射到同一请求身份结构。

#### Scenario: Successful API request captured

- **WHEN** an authenticated POST request to `/api/agents` returns 201
- **THEN** the middleware SHALL create an audit event with `action="agent.create"`, `success=true`, `response_status=201`

#### Scenario: Failed API request captured

- **WHEN** an authenticated DELETE request to `/api/agents/{id}` returns 404
- **THEN** the middleware SHALL create an audit event with `success=false`, `response_status=404`, `error_code="NOT_FOUND"`

#### Scenario: Unauthenticated request excluded from audit

- **WHEN** an unauthenticated request hits any endpoint
- **THEN** the middleware SHALL create an audit event with `user_id=NULL`, `action="api.unauthenticated"`

#### Scenario: 审计事件关联真实操作者

- **WHEN** 分别使用 JWT 与数据库 API key 认证的用户发起管理操作（如创建 agent）
- **THEN** 对应审计事件的 `user_id`/`org_id`/`workspace_id` 与认证身份一致，且 `metadata` 含认证方式（`jwt` / `api_key`）

#### Scenario: 认证失败记录类型而非凭据

- **WHEN** 携带无效 Bearer 凭据的请求被 401 拒绝
- **THEN** 审计事件记录失败类型（`metadata.auth_failure="invalid_credentials"` 或等价错误码），事件中不出现原始 token 或 API key 字符串

#### Scenario: MCP 请求复用同一身份结构

- **WHEN** 已认证的 MCP 请求（经 `/mcp` 传输认证）被审计
- **THEN** 审计事件的主体字段与 REST 请求使用同一 `AuthContext` 结构（同一 `request.state.auth_context` 键）
