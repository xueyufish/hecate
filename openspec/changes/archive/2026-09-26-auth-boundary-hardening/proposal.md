# Proposal: auth-boundary-hardening

## Why

外部安全评审的 P0 #2–#5，四项认证/授权边界缺陷：

- **#2 系统身份空值推导提权**：`AuthContext.is_system_scope` 把 `workspace_id is None` 判为系统权限（`core/auth_context.py:44`）；登录服务对无成员关系用户照常签发无 workspace 的 JWT（`enterprise/services/auth/service.py` 的 `_resolve_workspace_context` 返回全 None 后仍建 token）。该 JWT 使 RBAC 依赖（viewer/editor/admin）全部放行、agent 列表跳过租户过滤、组织端点绕过 owner 校验。同时 `APIKeyAuthProvider` 把数据库 SYSTEM-scope key 直接授予系统权限——而铸造口子在 P0 #1 之前完全开放，存量 key 来源不可信。chat 端点 `_load_agent_or_404` 不校验 workspace，跨租户可直接对话。
- **#3 登录组织信息错误 + 撤销不生效**：`_resolve_workspace_context` 把 `membership.workspace_id` 返回两次充当 org_id（`service.py:71`），首次登录的 org_id 错误；查询不过滤软删除成员/workspace；`refresh_tokens` 原样沿用旧 claims 不重查成员资格；`JWTAuthProvider` 只查 `user.active`——移除成员或降权后，旧 access token（30 分钟）与 refresh token（7 天）继续携带旧角色通行。
- **#4 MCP 无鉴权入口**：`verify_mcp_auth` 全仓库零调用方；`/mcp` 挂载仅在 `GATEWAY_ENABLED=true`（默认 False）时才有 Gateway 中间件，默认配置下传输层无任何认证；`MCP_AUTH_TYPE` 无实效。工具内部信任调用参数（`agent_list` 的 workspace_id 来自调用方）。
- **#5 模型供应商使用旧鉴权**：`enterprise/api/model_providers.py` 11 处端点用已废弃的 `verify_api_key`——接受任何有效 JWT 但不检查管理权限或用户当前状态（停用用户的旧 JWT 可继续操作）。

## What Changes

- **#2**：`is_system_scope` 语义收窄为仅 `api_key_scope == "system"`；该 scope 只授予部署期 env key（`HECATE_API_KEYS`）。数据库 SYSTEM-scope key 在认证阶段即拒绝（401），存量 key 失效，配套审计 SQL 与迁移说明。无 workspace 的 JWT 用户成为受限身份：RBAC 依赖 403、资源列表为空、组织端点仅 owner 可操作。agent 列表/详情/chat 按服务端身份强制 workspace 过滤（跨租户 404，不泄露存在性）。
- **#3**：登录解析 join `WorkspaceModel` 返回真实 org_id 并过滤软删除（成员与 workspace）；`refresh_tokens` 按数据库现状重新解析成员资格后签发；`JWTAuthProvider` 在携带 workspace claim 时校验成员行存在、未删除、角色与 claims 一致，不一致 401。
- **#4**：`/mcp` 挂载即强制传输层认证（Starlette 中间件包装，复用统一 provider 链），失败 401；`MCP_AUTH_TYPE=none` 仅作显式 dev 逃生口并告警。MCP tools 经 `get_http_request()` 读取服务端解析的 AuthContext；所有 catalog 工具的租户过滤以服务端身份为准，调用方传入的 workspace 参数不再作为授权依据。
- **#5**：`model_providers` 变更类端点（create/update/delete/test/publish/unpublish/model 增删）迁至 `require_platform_admin`；读取类端点迁至 `get_auth_context`（响应本就不含凭据）。其余三个 legacy 依赖消费方（`channel/api/v1/models.py`、`studio/api/orchestration_templates.py`、`collaboration_patterns.py`、`agent_templates.py`）为后续批次。
- **BREAKING**：数据库 SYSTEM-scope API key 全部失效；无 workspace JWT 的权限大幅收缩；`/mcp` 默认要求认证；跨租户 agent 访问从可行变为 404。

## Capabilities

### New Capabilities

- `user-authentication`: 登录/刷新/请求期三段的成员资格解析与撤销语义（#3）。
- `mcp-server-auth`: MCP 传输层强制认证与工具的服务端身份执行（#4）。
- `model-provider-management`: 模型供应商管理的权限分层（#5）。

### Modified Capabilities

- `platform-admin`: 平台管理员身份解析 requirement 收紧——系统身份仅来自部署期配置，数据库 SYSTEM-scope key 在认证阶段即拒绝。
- `tenant-data-isolation`: 新增受限身份（无 workspace 用户）的访问边界与资源端点的服务端强制租户过滤。

## Impact

- **代码**：`core/auth_context.py`、`enterprise/auth/api_key_provider.py`、`enterprise/auth/jwt_provider.py`、`enterprise/auth/token.py`（不动）、`core/deps_workspace.py`（抽出共享认证函数）、`packages/hecate-enterprise/.../services/auth/service.py`、`studio/api/agents.py`、`channel/api/v1/agents.py`、`tools/mcp/server.py` + 新增 `tools/mcp/auth_middleware.py`、`main.py`（/mcp 挂载处）、`enterprise/api/model_providers.py`、`scripts/audit_system_api_keys.sql`（新增）、`.env.example` / MCP 文档。
- **行为**：见各 BREAKING 项；组织端点 owner 校验对 env-key 调用方保持放行，其余收窄。
- **测试**：`test_auth.py`、`test_api_key_api.py`、`test_backup_api.py`（env-key 路径回归）、`test_mcp_server.py`、model_providers 测试、conftest 补 fixture。
- **无数据库迁移**；存量 DB SYSTEM key 靠认证层拒绝（不需要改数据，但提供审计 SQL 供运维清点）。
