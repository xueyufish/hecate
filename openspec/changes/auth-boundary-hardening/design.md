# Design: auth-boundary-hardening

## Context

四个缺陷的代码事实（均已核实）：

- **#2**：`core/auth_context.py:44` 的 `is_system_scope` 为 `api_key_scope == "system" or workspace_id is None`；`deps_workspace.py` 三个 RBAC 依赖与 `enterprise` 包的 orgs/workspaces owner 校验都消费它。`APIKeyAuthProvider.authenticate`（`api_key_provider.py:82-92`）把 DB SYSTEM key 映射为 `api_key_scope="system"`。登录对无成员用户签发全 None claims 的 JWT（`service.py:_resolve_workspace_context` 返回 `(None, None, None)` 后 `login` 照常建 token）。`studio/api/agents.py:177-185` 列表过滤条件为 `if ctx.workspace_id is not None`——None 时不过滤（现依赖错误的 is_system_scope"恰好"拦截，语义修复后会暴露）。`channel/api/v1/agents.py:99` `_load_agent_or_404` 无 workspace 校验。
- **#3**：`service.py:71` 返回 `membership.workspace_id, membership.workspace_id, ...`（org_id 错位）；查询无 deleted 过滤（同文件 `get_user_workspaces` 有）；`refresh_tokens` 直接复用 payload 旧 claims；`jwt_provider.py:58-61` 只查 `user.active`。
- **#4**：`tools/mcp/auth.py` 的 `verify_mcp_auth` 零调用方；`main.py:537-545` 挂载 `_mcp.http_app(path="/")` 无认证层；fastmcp 4.0.0b4 提供 `get_http_request()`（`fastmcp.server.dependencies`）。工具直接查库无租户过滤（`server.py` 的 agent/knowledge/tool 目录工具）。
- **#5**：`enterprise/api/model_providers.py` 11 处 `Depends(verify_api_key)`；`core/deps.py:36` 的 `verify_api_key` 只做签名验证。`ModelProviderModel` 无 workspace 字段（平台级资源）；`ModelProviderReadSchema` 不含凭据字段。

另一处 P0 #1 的遗留缺口：`PLATFORM_ADMIN_API_KEYS` 令牌在真实请求流中会被 `get_auth_context` 的 provider 链 401（它不是 JWT、不在 `HECATE_API_KEYS`、不是 DB key）——P0 #1 测试经 conftest override 绕过了这条链，生产流量走不通。本 change 一并修复。

## Goals / Non-Goals

**Goals:**

- 系统级身份只来自部署期 env 配置；空值推导与 DB SYSTEM key 两条提权路径全部关闭。
- 令牌生命周期内权限撤销生效（请求期成员校验 + 刷新重解析）。
- `/mcp` 传输层强制认证，工具以服务端身份执行。
- model_providers 权限分层；`PLATFORM_ADMIN_API_KEYS` 令牌走通统一认证链。

**Non-Goals:**

- 不迁移其余 legacy 依赖消费方（`channel/api/v1/models.py`、`studio/api/orchestration_templates.py`、`collaboration_patterns.py`、`agent_templates.py`）——后续批次，迁完才删 `verify_api_key`。
- 不引入权限版本号/成员缓存失效框架——请求期 join 校验已达撤销语义，缓存优化留给身份管线（11.16/11.17）。
- 不改 JWT secret 派生机制（`_get_secret` 从 API keys 推导是独立隐患，另行立项）。
- 不做 MCP 工具级细粒度权限矩阵（工具内按 workspace 过滤 + 归属校验为本批边界；逐工具 ACL 留给权限框架）。
- 不处理 `MCP_AUTH_TYPE` 的 jwt/api_key 分支语义——统一认证链取代它，`none` 之外取值仅告警不分支。

## Decisions

### D1: `is_system_scope` 收窄为 `api_key_scope == "system"`，且该 scope 仅 env 来源可获得

- `AuthContext.is_system_scope` 删除 `or self.workspace_id is None` 分支。
- `APIKeyAuthProvider` 对 DB SYSTEM key **返回 None（401）**并记 warning——备选"保留认证但取消绕过"被否决：铸造口子曾对全体用户开放，存量 key 来源不可信，保留一个"已认证但处处 403"的身份只增加困惑；直接失效 + 审计 SQL 让运维清点迁移。
- env 两个清单（`HECATE_API_KEYS`、`PLATFORM_ADMIN_API_KEYS`）都在统一认证链的 env fallback 中建立 system-scope 身份：把 `_resolve_env_api_key` 扩展为遍历两个清单。这同时修复 P0 #1 遗留的平台令牌 401 缺口，并让 `ensure_platform_admin` 的令牌比对与认证层语义对齐。

### D2: 请求期成员校验放 `JWTAuthProvider`，单条 join 查询

workspace claim 存在时：`select(WorkspaceMemberModel, WorkspaceModel).join(...).where(user_id, workspace_id, member.deleted==False, ws.deleted==False)`，校验成员存在且 `member.role.value == role claim`。不一致 401。成本：每次 JWT 请求一条索引查询（`jwt_provider` 本就查 `UserModel`），量级可接受；`require_*` 依赖内的角色判断不变。`switch_workspace`/登录路径复用同一个解析函数（`service.py` 抽 `_resolve_workspace_context` 的共享版本，join Workspace 取真实 org_id + 双重 deleted 过滤）。

### D3: MCP 认证 = 挂载点 Starlette 中间件 + 统一认证链复用

- 从 `deps_workspace` 抽出 `async def authenticate_bearer(token: str | None, db) -> AuthContext | None`（provider 链 + env fallback，不含 HTTPException 包装），`get_auth_context` 与 MCP 中间件共用——单一真相，避免两套解析漂移。
- 新增 `tools/mcp/auth_middleware.py`：纯 Starlette 中间件，包装 `http_app` 返回的 ASGI app（`app.mount("/mcp", AuthMiddleware(_mcp_app))` 形式，不依赖 fastmcp 内部 API）；凭据缺失/解析失败返回 401 JSON（错误体与 REST 一致）；成功则 `request.state.auth_context = ctx` 并放行。`MCP_AUTH_TYPE=none` 显式配置时跳过并打 warning（启动时一次）。
- 工具侧：通过 `get_http_request()` 读 `request.state.auth_context`；无 ctx（进程内直调）返回 `{"error": "unauthenticated"}`。删除未接线的 `tools/mcp/auth.py`。
- 备选 fastmcp 原生 `auth=` TokenVerifier 被否决：4.0.0b4 的 auth 面向 OAuth 资源服务器语义，与仓库的 provider 链模型不匹配，包装成本高于自写 40 行中间件。

### D4: 工具租户过滤——服务端身份覆盖参数

- `agent_list`：删除 `workspace_id` 参数（破坏性签名变更，MCP 工具目录随之更新），无条件 `workspace_id == ctx.workspace_id`；system-scope ctx 才可全量。
- `agent_create`：写入 `workspace_id=ctx.workspace_id`（当前为 NULL——顺带修复归属缺失）；受限身份（workspace None）拒绝。
- `agent_chat`/`session_create`/`session_resume`/`session_list`/`conversation_history`：校验目标 agent/session/conversation 的 workspace 归属，不符返回不存在。
- `knowledge_*`/`tool_*`：列表加 `workspace_id == ctx.workspace_id` 过滤；读/写单个资源校验归属。与 REST 侧 `tenant-data-isolation` 的既有语义一致（bundled/zero-uuid 共享资源的处理沿用 skill 的 `in [ws, zero]` 惯例，如适用）。

### D5: model_providers 分层——变更走平台管理员，读取走已认证

- 8 个变更端点（create/update/delete provider、test provider、discover→含在 create 内、add/update/delete/publish/unpublish model、test model）→ `Depends(require_platform_admin)`。
- `list_providers`/`list_models` → `Depends(get_auth_context)`。
- 响应 schema 本就不含 `api_key_encrypted`，读取放宽无泄露风险。
- 停用用户拦截由 `get_auth_context` → `JWTAuthProvider` 的 active 检查天然覆盖（无需额外逻辑）。

### D6: 审计与迁移

- 新增 `scripts/audit_system_api_keys.sql`：列出存量 SYSTEM-scope key（`SELECT id, name, key_prefix, created_by, created_at FROM api_keys WHERE scope='system' AND deleted=false AND is_active=true`）。
- 文档（`docs/reference/rest-api.md` 的 MCP/system 段落 + `.env.example` 的 MCP_AUTH_TYPE 注释）更新行为变更。
- DB SYSTEM key 无需数据迁移——认证层已拒绝；运维按审计结果决定是否物理清理。

## Risks / Trade-offs

- [DB SYSTEM key 集成断裂] 使用 DB system key 的既有集成开始 401 → Mitigation：PR 与文档给出迁移路径（改用 env `HECATE_API_KEYS`/`PLATFORM_ADMIN_API_KEYS` 或 workspace-scoped key）；审计 SQL 清点影响面。
- [MCP 客户端断裂] 本地开发/内部 MCP 客户端未带凭据 → Mitigation：`MCP_AUTH_TYPE=none` 显式逃生口 + 启动告警；默认强制符合评审验收。
- [请求期成员校验的额外查询] 每次带 workspace claim 的 JWT 请求多一条 join → Mitigation：索引命中、单行返回；与已有的 per-request user 查询同量级。
- [agent_create 补 workspace_id] 新建的 agent 从 NULL 变为有归属；NULL agent 对既有列表过滤（`workspace_id ==`）不可见——但现状是越权可见，属于修复的一部分。
- [chat 跨租户 404] 之前"能访问"的跨租户 agent 变 404 → 这正是验收目标；`tenant-data-isolation` 已有 404 语义惯例。
- [无 workspace 用户的行为收缩] 未加入组织的用户几乎无可用端点 → 符合评审验收（"只能访问个人资料、加入组织等有限接口"）；profile/邀请接受端点不受 RBAC 依赖保护，回归测试覆盖。

## Migration Plan

1. 部署前运行 `scripts/audit_system_api_keys.sql` 清点 DB SYSTEM key 及其 `created_by`。
2. 受影响的集成改用 env 引导 key（system）或 workspace-scoped key。
3. MCP 客户端配置 Bearer 凭据；纯本地开发可显式设 `MCP_AUTH_TYPE=none`。
4. 无 DB 迁移；回滚 = revert。

## Open Questions

无——四个修复点的资源语义（provider 平台级、agent/KB/tool workspace 级）均已从模型字段确认。
