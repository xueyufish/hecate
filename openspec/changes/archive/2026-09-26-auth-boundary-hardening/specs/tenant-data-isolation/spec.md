# tenant-data-isolation Delta

## ADDED Requirements

### Requirement: 受限身份的访问边界

不携带 workspace 上下文的已认证用户（如新注册、未加入组织、或成员关系已被移除的用户）SHALL 视为受限身份：系统级身份推导 SHALL NOT 因 workspace 缺失而成立；RBAC 依赖（viewer/editor/admin）SHALL 返回 403；资源列表端点 SHALL 返回空结果而非全量数据；组织/workspace 管理端点的 owner 校验语义不变。

#### Scenario: 未加入组织的用户请求 RBAC 保护端点

- **WHEN** 持有不含 workspace claim 的有效 JWT 的用户调用要求 viewer/editor/admin 角色的端点
- **THEN** 返回 403 Forbidden

#### Scenario: 未加入组织的用户列出资源

- **WHEN** 受限身份的用户调用 agent 等资源列表端点
- **THEN** 返回空结果，而非其他租户的资源

### Requirement: 资源端点的服务端强制租户过滤

资源端点（REST 与 MCP 工具）SHALL 以服务端解析的认证身份（`AuthContext.workspace_id`）作为租户过滤的唯一权威来源；调用方在请求体或工具参数中提供的 workspace 标识 SHALL NOT 作为授权依据。系统级身份（env key）SHALL 保持跨租户可见性；非系统身份对不属于其 workspace 的单个资源的访问 SHALL 返回 404（不泄露存在性）。

#### Scenario: 跨租户 agent 对话被拒

- **WHEN** workspace A 的用户调用 chat 端点访问 workspace B 的 agent
- **THEN** 返回 404，agent 的存在性不被泄露

#### Scenario: 伪造 workspace 参数不改变过滤范围

- **WHEN** workspace A 的用户在请求中提供 workspace B 的标识（如工具参数或查询参数）
- **THEN** 服务端仍按调用者身份的 workspace 过滤，返回结果不包含 workspace B 的资源

#### Scenario: 无 workspace 用户调用 chat

- **WHEN** 受限身份的用户调用 chat 端点访问任意 agent
- **THEN** 返回 404，不执行对话
