# Design: step11-agent-version-access-boundary

## Context

`agent_versions.py` 的 13 个端点全部注入 `AuthContext`，但仅 commit/rollback 取 `ctx.user_id` 作 actor、publish 传入 `auth_context`（#276 在服务层做归属校验）、publications 传入 `workspace_id`/`is_system_scope`；update/delete 与 6 个读端点完全忽略身份中的授权信息。服务层 `_get_agent`（`versioning.py:948`）按 id + 未删除查询，无 workspace 过滤。

既有可复用构件：

- `ensure_workspace_role(ctx, minimum)`（`core/deps_workspace.py:234`）：`is_system_scope` 豁免；缺 workspace/role 拒绝；`WorkspaceRole._role_level` 排序（VIEWER:0/EDITOR:1/ADMIN:2）。领域异常 `WorkspaceRoleRequiredError`（`PermissionError` 子类）刻意不含 HTTP 语义，供非 HTTP 调用方复用。
- `require_workspace_viewer/editor/admin` FastAPI 依赖：`WorkspaceRoleRequiredError` → 403 `FORBIDDEN` 的既有映射契约。
- `publish_version` 的归属校验（`versioning.py:572-577`）：非 system + workspace 不匹配 → `ValueError("Agent ... not found")` → API 层 `_not_found` → 404。

内部生产调用方 `channel/api/v1/channels.py:136`、`ops/evaluation/engine.py:395`、`studio/workflows/execution_service.py:318` 仅调用 `resolve()`（运行时版本解析），不在 13 个生命周期方法面上。测试直接构造 `AgentVersionService` 调用 `commit_version` 等方法且不传身份（`test_agent_publication_receipt.py:59` 等）；共享 `client` fixture 默认身份为同 workspace ADMIN（`tests/conftest.py:221-230`）。

## Goals / Non-Goals

**Goals**

- 13 个生命周期端点统一两级校验（归属 → 404；角色 → 403），判定收敛在服务层一处。
- 零破坏：内部调用方（`resolve()`）、直接构造服务的既有测试、默认 ADMIN 身份的 API 回归全部不变。
- 错误面与 #276 完全一致：跨 workspace 一律 not found，不泄露存在性。

**Non-Goals**

- 不改 `resolve()`、评测/门禁判定、`publish_gate.py`、数据模型、迁移。
- 不覆盖版本生命周期之外的端点（ evaluations、memory 等同类缺口在 verification.md 登记为已知限制）。
- 不引入新角色层级、权限模型或资源级 ACL（workspace 归属即边界）。

## Decisions

### D1. 服务层共享 helper，而非 FastAPI 依赖层校验

新增 `_ensure_access(agent, auth_context, *, minimum_role)`：归属判定（非 system + workspace 不匹配或缺失 → `ValueError("Agent ... not found")`）+ `ensure_workspace_role(ctx, minimum_role)`。13 个生命周期方法在 `_get_agent` 之后调用。

- **为什么不在依赖层**：归属判定需要先加载 Agent（数据库访问），依赖层做会重复查询或把 ORM 对象塞进 Depends 链；且 MCP 工具（`tools/mcp/server.py` 已 import `ensure_workspace_role`）等服务层调用方将来可直接复用同一判定，符合 `WorkspaceRoleRequiredError` 的设计意图（其 docstring 明示服务非 HTTP 调用方）。
- **为什么归属在角色之前**：跨 workspace 调用方应看到 not found（不泄露存在性），而非 403（泄露"该 Agent 存在但你不许看"）。同 workspace 内角色不足才 403。
- **替代方案**：仅用 `Depends(require_workspace_viewer/editor)` 做角色 + 保留各端点散落的归属检查——被否决：角色与归属分置两层，12 个端点的归属检查无处安放，且未来非 HTTP 入口拿不到统一判定。

### D2. 既有生命周期方法兼容可信内部调用，发布历史保留缺省拒绝

生命周期方法签名使用 `auth_context: AuthContext | None = None`；除发布历史查询外，为 `None` 时沿用既有可信内部调用兼容路径。发布历史查询原本缺失身份即拒绝，本次保持 `auth_context=None` 时 not found，不扩大其默认读取权限。这沿用 `publish_version` 自 #276 起的显式契约（"API callers must supply their auth context; omitted contexts are reserved for trusted internal calls"）。

- **为什么不设为必填**：`channel`/`evaluation engine`/`workflow execution_service` 虽只调 `resolve()`，但测试直接构造服务调用 `commit_version` 等（约 10 处）；必填会把大量既有调用点改成语义上并不可信内部调用的形式，反而稀释"省略 = 可信"的契约。安全性由 API 层保证——全部 13 个端点都传入 `ctx`，并有测试逐端点断言。
- **例外（独立审查修正）**：`list_publications` 自 #276 起对缺失身份 fail-closed，从未接受过省略身份的调用——该方法的 `auth_context=None` 保持拒绝（与既有契约一致），内部快照读取须显式传 system-scope 身份；其余 12 个生命周期方法维持"省略 = 可信内部路径"。
- **风险与对策**：未来新端点忘传 `auth_context` 会静默绕过 → 路由清单守护测试断言"矩阵覆盖集 == router 注册路由集"，新路由必须同时进入身份与角色矩阵才能通过；逐端点跨 workspace 断言（身份矩阵）兜底。

### D3. 读 VIEWER / 写 EDITOR 的角色映射

| 端点组 | 最低角色 | 理由 |
|---|---|---|
| list/detail/skill-closure/drift/publications/published/diff/version-status | VIEWER | 只读；VIEWER 是最低 workspace 角色，任何有成员身份的调用方满足 |
| commit/update/delete/publish/rollback | EDITOR | 全部产生持久变更；rollback 新建版本，按变更对待（spec 明示） |

与 `memory_governance.py` 的内联 `ctx.role not in (ADMIN, EDITOR)` 及 MCP `_require_editor` 的既有实践一致。

### D4. API 层错误映射

`agent_versions.py` 新增 `_forbidden(exc)` helper（镜像 `_not_found` 的 canonical error body：`{"error": {"code": "FORBIDDEN", ...}}`）；各端点 `except WorkspaceRoleRequiredError` → 403。归属 `ValueError` 走既有 `_not_found` → 404。publications 迁移到共享 helper（替代 #276 的方法内联检查），可观察行为不变：其原检查（缺失身份 / 非 system 缺 workspace / 跨 workspace → ValueError）全部保留——缺失身份拒绝在 helper 之前显式保留（见 D2 例外），其余是 helper 归属判定的子集，helper 额外的 VIEWER 门槛只对"有 workspace_id 但无角色"的身份生效，而身份解析层（JWT/API key provider）保证该状态不出现。

### D5. 测试落点

新增 `tests/test_api/test_agent_version_access_boundary.py`，自包含构造第二 workspace 与 member（不改动公共 conftest，沿用 #276 publication-receipt 测试的隔离做法）：跨 workspace 读/写矩阵（每端点至少一条）、viewer/editor/admin 三级矩阵、缺 workspace 身份、system-scope 通行、逐端点"API 必传身份"断言、publish/publications 回归锚点。既有三个版本化测试文件作为回归收集，预期零改动。

## Risks / Trade-offs

- [新端点忘传 `auth_context` 静默绕过] → 逐端点跨 workspace 断言测试 + design 契约注释（省略仅限可信内部路径）。
- [publications 语义漂移] → 保留 #276 既有 publications 断言作为回归，验证迁移到 helper 后行为逐字段一致。
- [某些既有调用方以"任意 Agent id"调用服务方法（如清理脚本）] → 全库 grep 确认生产调用面仅 `resolve()` 与 API 层；`auth_context=None` 路径保持兼容。
- [403 泄露同 workspace 内 Agent 存在性] → 可接受：调用方本已是同 workspace 成员，成员列表对该 workspace 管理员可见；平台既有 `require_workspace_*` 语义相同。

### D6. 主 spec 结构修复（归档前置条件）

`openspec/specs/agent-versioning/spec.md:126` 残留 `## ADDED Requirements` 增量头（先前某次归档同步把 delta 节原样追加进了主 spec），其后 10 条 requirement（回滚/diff/删除约束/解析接缝/闭包系列/发布历史）位于主 `## Requirements` 节之外（主节内仅 3 条）；`openspec show` 宽松地显示全部 13 条，但归档解析器只认主节且本次 `openspec validate --strict` 已报告归档将拒绝 delta。修复是纯结构操作：删除该残留头与其后空行，使 10 条 requirement 回到 `## Requirements` 节——不改动任何 requirement/scenario 文本。在 apply 阶段执行并以 `openspec show agent-versioning --type spec` 断言 13 条 requirement 条目不变、`validate --strict` 警告消失。

## Migration Plan

无迁移、无配置变更。单 PR 合入即生效；回滚 = revert PR（无持久状态变化）。

## Open Questions

（无——错误码、角色映射、豁免规则均已在 #276 与 `deps_workspace.py` 既有契约中确定。）


## 审查澄清：发布历史查询保留既有缺省拒绝

list_publications 原本在缺少 workspace 身份时拒绝读取。统一 helper 不得把这一查询改成省略身份即全局放行：此入口保留 auth_context=None 时的 not found，可信内部查询也须显式携带身份（例如 system-scope）。其余既有生命周期方法的省略身份兼容路径不变。测试读取审查快照时使用显式 system-scope。
