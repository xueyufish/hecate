# Tasks: auth-boundary-hardening

## 1. P0 #2 —— 系统身份推导收窄

- [x] 1.1 `core/auth_context.py`：`is_system_scope` 删除 `or self.workspace_id is None` 分支，docstring 更新语义。验证：`rg "workspace_id is None" src/hecate/core/auth_context.py` 无 is_system_scope 相关残留。
- [x] 1.2 `core/deps_workspace.py`：抽出 `authenticate_bearer(token, db) -> AuthContext | None`（provider 链 + env fallback 共享函数）；`_resolve_env_api_key` 扩展为同时接受 `HECATE_API_KEYS` 与 `PLATFORM_ADMIN_API_KEYS`（后者日志注明 platform bootstrap）；`get_auth_context` 改为调用共享函数。验证：`mypy src/hecate/core/deps_workspace.py` 通过。
- [x] 1.3 `enterprise/auth/api_key_provider.py`：DB SYSTEM-scope key 返回 None（401）并记 warning；workspace key 路径不变。验证：单测覆盖（见 3.x）。
- [x] 1.4 新增 `scripts/audit_system_api_keys.sql`（存量 SYSTEM key 清点查询 + 注释说明处置建议）。验证：SQL 语法自查。

## 2. P0 #2 —— 资源端点服务端租户过滤

- [x] 2.1 `studio/api/agents.py`：列表与各资源查询的过滤条件从 `if ctx.workspace_id is not None` 改为无条件按 `ctx.workspace_id` 过滤（system-scope 例外）；受限身份返回空结果。验证：`tests/test_api` 中 agents 相关用例回归。
- [x] 2.2 `channel/api/v1/agents.py`：`_load_agent_or_404` 增加 workspace 归属参数，非本 workspace（且非 system-scope）返回 404；chat 与 session 路径传 ctx。验证：跨租户 chat 负例测试（见 3.2）。

## 3. P0 #3 —— 认证生命周期

- [x] 3.1 `packages/hecate-enterprise/.../services/auth/service.py`：`_resolve_workspace_context` join `WorkspaceModel` 返回真实 org_id、过滤成员与 workspace 软删除；抽共享解析供 `switch_workspace` 复用；`refresh_tokens` 按数据库重新解析成员资格（不再沿用旧 claims），无有效成员资格时签发受限身份。验证：`pytest tests/test_api/test_auth.py` 全绿。
- [x] 3.2 `enterprise/auth/jwt_provider.py`：workspace claim 存在时校验成员行存在、未删除、workspace 未删除、角色与 claims 一致，不满足 401；无 workspace claim 走受限身份。验证：`pytest tests/test_api/test_auth.py tests/test_api/test_backup_api.py` 全绿。
- [x] 3.3 测试：`test_auth.py` 补 org_id 正确性、软删除过滤、降权刷新、移除成员后旧 access/refresh 失效、无 workspace 用户受限（RBAC 403、列表空）；`test_api_key_api.py` 补 DB SYSTEM key 认证 401；agents 跨租户 chat 404 负例。验证：全绿。

## 4. P0 #4 —— MCP 传输层认证与服务端身份

- [x] 4.1 新增 `tools/mcp/auth_middleware.py`：Starlette 中间件，凭据缺失/解析失败返回 401 JSON（与 REST 错误体同构），成功写入 `request.state.auth_context`；`MCP_AUTH_TYPE=none` 显式跳过 + 启动 warning。`main.py` 挂载处包装 `_mcp_app`。删除 `tools/mcp/auth.py`。验证：模块级单测 + `rg verify_mcp_auth` 无残留。
- [x] 4.2 `tools/mcp/server.py`：工具经 `get_http_request()` 读取 `request.state.auth_context`；无 ctx 返回 unauthenticated 错误；`agent_list` 移除 workspace_id 参数并按 ctx 过滤；`agent_create` 写入 ctx.workspace_id；chat/session/conversation/knowledge/tool 工具按归属校验与过滤。验证：`pytest tests/test_services/test_mcp_server.py` 更新后全绿。
- [x] 4.3 测试：`test_mcp_server.py` 补矩阵——匿名 401、有效 JWT 通过、伪造 workspace 参数被覆盖、跨租户 agent_chat 拒绝、`MCP_AUTH_TYPE=none` 放行。验证：全绿。

## 5. P0 #5 —— model_providers 权限分层

- [x] 5.1 `enterprise/api/model_providers.py`：8 个变更端点 `Depends(verify_api_key)` → `Depends(require_platform_admin)`；`list_providers`/`list_models` → `Depends(get_auth_context)`；清理 imports。验证：`rg verify_api_key src/hecate/enterprise/` 无残留。
- [x] 5.2 测试：viewer 变更 403、平台管理员变更 200/201、停用用户 401、读取需认证、响应无凭据字段。验证：`pytest tests/test_api/test_e2e_model_provider.py`（及新增用例）全绿。

## 6. 文档与验证

- [x] 6.1 文档：`.env.example` 的 `MCP_AUTH_TYPE` 注释更新（默认强制认证、none 为 dev 逃生口）；`docs/reference/rest-api.md` 的 MCP 段落与 system 段落补充认证要求。验证：文档自查。
- [x] 6.2 门禁：`ruff check src/hecate/ tests/`、`ruff format --check src/ tests/`、`mypy src/`、`python -m pytest tests/test_api tests/test_services tests/test_enterprise -q`。验证：全部 0 错误。
- [x] 6.3 `openspec validate auth-boundary-hardening` 通过。
- [x] 6.4 修复 CI 全量回归暴露的 provider 链污染：`tests/test_auth/test_resolver.py` 的 autouse fixture 清空全局 provider 注册表且不恢复，而 `deps_workspace._ensure_providers` 用一次性布尔标记跳过重注册，导致其后所有真实链认证（MCP transport 4 例）401。改为 fixture 快照恢复 + `_ensure_providers` 在注册表为空时自愈。验证：`tests/test_api/test_auth.py + tests/test_auth/ + tests/test_services/test_mcp_server.py` 同进程组合运行全绿；CI 门禁范围已含 `tests/test_auth/`，不再有此盲区。
