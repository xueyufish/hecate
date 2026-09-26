# Proposal: backup-api-authz

## Why

备份/恢复接口的鉴权是空实现：`ops/api/backup.py` 的 `_require_platform_admin()` 为占位 `pass`，`list`/`get` 端点连占位依赖都没有，路由在主应用无条件挂载——任何能到达 API 的调用者（含匿名）都可以列出备份并触发全平台恢复。`backup-api` spec 早已写明"仅 Platform Admin 可访问"，但仓库不存在任何平台管理员概念可供实现。同时 `POST /api/api-keys` 允许任意已认证用户自铸 `scope=system` 的 API key，即使备份接口接入门禁，普通用户仍可先自铸系统 key 再绕过——本 change 不关这个铸造口子，备份门禁就形同虚设。

## What Changes

- 新增部署期平台管理员引导配置（`core/config.py`）：`PLATFORM_ADMIN_API_KEYS`（逗号分隔运维令牌，常量时间比对）与 `PLATFORM_ADMIN_EMAILS`（JWT 用户邮箱白名单）。在此两清单皆为空时无人是平台管理员（fail-closed）。
- `deps_workspace.py` 新增 `ensure_platform_admin` 复用校验与 `require_platform_admin` FastAPI 依赖：凭据缺失 401，已认证但非平台管理员 403。数据库 system-scope API key 在身份管线（11.16/11.17）落地前不视为平台身份。
- `backup.py` 五个端点全部挂载 `require_platform_admin`，删除 `_require_platform_admin` 占位；restore 保留 `confirm=true` 二次确认。独立的 `backup:restore` 细粒度权限推迟到权限框架落地后。
- **BREAKING** `POST /api/api-keys`：铸造 `scope=system` 的 key 仅限平台管理员；普通用户铸造 workspace-scoped key 的行为不变。
- **BREAKING** 认证语义修正：`deps_workspace.get_auth_context` 的 Bearer 凭据缺失从 HTTPBearer 默认 403 改为 401（`auto_error=False` + 显式 401）。
- 测试：重写 `tests/test_api/test_backup_api.py` 为鉴权验收矩阵（匿名 / workspace admin / 平台管理员令牌 / 平台管理员邮箱 × 全部端点，含"拒绝不触发备份/恢复函数"断言）；`test_api_key_api.py` 补 system 铸造负例；conftest 增加匿名 client 与平台管理员配置支撑。
- 文档：`.env.example` 与 `docs/reference/rest-api.md` 补充两个新环境变量与备份接口鉴权说明。

## Capabilities

### New Capabilities

- `platform-admin`: 平台管理员身份解析——部署期 env 引导（API key 令牌 + 用户邮箱白名单），以及 system-scope API key 铸造的平台管理员限制。

### Modified Capabilities

- `backup-api`: "Platform Admin 权限控制" requirement 收紧为可验收行为：覆盖全部五个 REST 端点（含 list/get，此前未规格化）；匿名 401、已认证非平台管理员 403；被拒绝的请求不得触发备份/恢复/验证函数。

## Impact

- **代码**：`src/hecate/core/config.py`（两个新配置 + list 属性）、`src/hecate/core/deps_workspace.py`（新依赖 + 401 语义）、`src/hecate/ops/api/backup.py`（接线 + 删占位）、`src/hecate/enterprise/api/api_keys.py`（system 铸造限制）。
- **API 行为**：`/api/system/backups*`、`/api/system/restore` 从事实开放变为平台管理员专属（与既有 spec 对齐）；所有经 `get_auth_context` 的端点对匿名请求返回 401 而非 403。已有部署若用脚本/CI 调备份接口，需配置 `PLATFORM_ADMIN_API_KEYS`。
- **测试**：`tests/test_api/test_backup_api.py` 重写；`tests/test_api/test_api_key_api.py` 增补；`tests/conftest.py` 增加 fixture。
- **文档**：`.env.example`、`docs/reference/rest-api.md`。
- **无数据库迁移**；不改动 `is_system_scope` 推导本身（P0 #2 的修复对象，另行立项）。
