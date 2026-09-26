# Design: backup-api-authz

## Context

- `src/hecate/ops/api/backup.py`：五个端点，其中三个调用占位 `_require_platform_admin()`（`pass`），两个（list/get）无任何鉴权；路由在 `main.py` 无条件挂载。
- 仓库不存在平台管理员概念：`UserModel` 无平台级角色字段，`WorkspaceRole` 仅 workspace 级（admin/editor/viewer），唯一的"系统身份"是 `AuthContext.api_key_scope == "system"`。
- 致命前置约束：`POST /api/api-keys`（`enterprise/api/api_keys.py`）允许任意已认证用户铸造 `scope=system` 的 key（`create_api_key` 仅要求 `get_auth_context`）。若备份门禁绑在 system key 上，等于没锁。
- `deps_workspace.get_auth_context` 经 `HTTPBearer()`（`auto_error=True` 默认）解析凭据，匿名请求得到的是 403 "Not authenticated" 而非 401。
- 测试基建：`tests/conftest.py` 的 `client` fixture 通过 `app.dependency_overrides[get_auth_context]` 注入 workspace-admin 上下文；`test_user_id` fixture 只建 `WorkspaceMemberModel`，不建 `UserModel` 行；现有 `test_backup_api.py` 全部用例以 workspace admin 身份直连 200——恰好固化了越权路径。

## Goals / Non-Goals

**Goals:**

- 备份/恢复五个 REST 端点全部位于平台管理员门禁之后，拒绝路径不触发任何备份/恢复函数。
- 平台管理员身份 fail-closed：只来自部署期配置（令牌 + JWT 邮箱白名单），空配置 = 无平台管理员。
- 关闭 system-scope API key 的自助铸造口子。
- 匿名请求统一返回 401（语义修正，非备份接口独有）。

**Non-Goals:**

- 不改 `AuthContext.is_system_scope` 的推导（P0 #2 立项处理）；本 change 只保证系统 key 不再能被普通用户新铸。
- 不引入权限字符串/细粒度 RBAC 框架（`backup:restore` 独立权限随之推迟）；env 双清单是将来拆分的天然接缝。
- 不给 DB system key 发放平台权限（等 11.16/11.17 身份管线）。
- 不动 CLI 备份命令、不做数据库迁移、不改备份编排器本身。

## Decisions

### D1: 平台身份 = 部署期 env 引导，不复用 `HECATE_API_KEYS`

新增 `PLATFORM_ADMIN_API_KEYS` 与 `PLATFORM_ADMIN_EMAILS` 两个独立配置。

- 为什么不复用 `HECATE_API_KEYS`：它已 deprecated（`deps.py` 明确标注），且其 system 语义与"平台管理员"混用会延续现有混乱；独立清单让运维可以单独轮换备份权限。
- 为什么 DB system key 不算平台身份：`create_api_key` 的铸造口子历史上完全开放，存量 key 的来源不可信；在身份管线落地并完成存量审计前，将其排除在平台边界之外是唯一安全选择。
- 备选（否决）：给 `UserModel` 加 `is_platform_admin` 列——需要迁移 + 管理界面 + 首个管理员的引导问题，超出本 change；env 引导是标准 bootstrap 模式。

### D2: 令牌比对用 `hmac.compare_digest` 常量时间比较

清单遍历 + `compare_digest`。备选（否决）：集合成员 `in`——与仓库现有 `api_keys_list` 用法一致但存在时序侧信道；备份操作是高价值目标，多写三行值得。

### D3: 邮箱路径经 DB 查询而非 JWT claims

JWT claims 不含 email（`create_access_token` 只签 sub/org/ws/role），因此邮箱白名单路径在依赖内按 `ctx.user_id` 查 `UserModel.email`。每次平台管理员请求多一次主键查询，运维接口频率下可忽略。非活跃用户已被 `JWTAuthProvider` 拒绝，白名单路径天然继承。

### D4: `ensure_platform_admin` 提为模块级公开函数，api-keys 端点直接调用

`require_platform_admin` 是 FastAPI 依赖（返回 `AuthContext`）；`create_api_key` 需要条件式校验（仅 `scope=system` 时），不能作为路由级依赖挂载——因此拆出 `ensure_platform_admin(credentials, ctx, db)` 抛 `HTTPException` 的核心函数，依赖和 api-keys 端点共用。备选（否决）：在 api-keys 内联一份判定逻辑——两处漂移风险。

### D5: 匿名 401 通过 `HTTPBearer(auto_error=False)` + 显式判断

只改 `deps_workspace.security_scheme`（`get_auth_context` 的子依赖）。`get_auth_context` 在 `credentials is None` 时返回与其他失败路径一致的 401 错误体。`deps.py` 的旧 `security_scheme`（`verify_api_key` 等 deprecated 依赖）不动——它们的迁移是 P0 #5 的范围。注意 FastAPI 的 dependency_overrides 会短路整个子依赖树，现有经 override 的测试不受影响；改后需全量跑 `tests/test_api/` 确认没有测试依赖 403 行为。

### D6: backup.py 用路由级依赖而非逐端点参数

`APIRouter(dependencies=[Depends(require_platform_admin)])`——五个端点一次覆盖，新增端点默认受保护，不留"忘记挂"的口子。端点函数签名不变。

## Risks / Trade-offs

- [部署脚本断裂] 已有用备份 API 的自动化脚本/CI 会开始收 401/403 → Mitigation：`.env.example` 与 `docs/reference/rest-api.md` 写明新变量与配置方法；PR 描述中给出迁移一行命令（设置 `PLATFORM_ADMIN_API_KEYS` + 请求头）。
- [env 清单运维负担] 邮箱/令牌轮换需要改配置重启 → 已知取舍，11.16/11.17 身份管线落地后由 DB 驱动替代；本 change 是止血。
- [401 语义变更波及面] 所有走 `get_auth_context` 的端点对匿名请求从 403 变 401 → Mitigation：全量跑 `tests/test_api/`；语义上 401 才符合 HTTP 语义与 spec 验收标准。
- [存量自铸 system key 仍有效] 本 change 只堵新铸造，已有越权 key 依然能通过 `is_system_scope` 获得高权限 → Mitigation：明确记录为已知残留风险，P0 #2 的修复对象；部署方可在 CHANGE 指引中用 SQL 审计/吊销存量 key（`api_keys` 表 `scope='system'`）。
- [多 worker 环境下 env 一致性] 所有 worker 必须加载相同 `PLATFORM_ADMIN_*` 配置 → 常规部署约束，写入文档。

## Migration Plan

1. 合入后，运维在部署环境设置 `PLATFORM_ADMIN_API_KEYS`（强随机令牌）和/或 `PLATFORM_ADMIN_EMAILS`。
2. 使用备份 API 的脚本在 Authorization 头改用该令牌。
3. 审计并吊销存量自铸 system key：`SELECT * FROM api_keys WHERE scope='system'`（吊销手段沿用现有 key 管理）。
4. 回滚 = revert 本 change（无 DB 迁移，无数据变更）。

## Open Questions

无——两个清单同时提供（用户已确认）；`backup:restore` 细粒度权限按 Non-Goals 推迟。
