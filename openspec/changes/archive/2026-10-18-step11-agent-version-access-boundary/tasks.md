# Tasks

## 1. 服务层共享校验

- [x] 1.1 在 `src/hecate/studio/agents/versioning.py` 新增 `_ensure_access(agent, auth_context, *, minimum_role)`：非 system-scope 且 workspace 缺失/不匹配 → `ValueError("Agent ... not found")`；否则 `ensure_workspace_role(ctx, minimum_role)`；`auth_context=None` 直接放行。docstring 写明省略 = 可信内部路径契约。验证：模块可导入、ruff/mypy 通过
- [x] 1.2 为 8 个读方法（`list_versions`/`get_version`/`version_drift`/`diff_versions`/`get_version_status` 及 skill-closure 复用的 `get_version` 路径）接入 `auth_context: AuthContext | None = None` 参数与 `_ensure_access(..., minimum_role=VIEWER)`；`get_published_version` 相关读路径经 `get_version_status`/`get_version` 覆盖。验证：既有单测零改动通过
- [x] 1.3 为 5 个写方法（`commit_version`/`update_version`/`delete_version`/`publish_version`/`rollback_to_version`）接入同参数与 `_ensure_access(..., minimum_role=EDITOR)`；`publish_version` 用 helper 替换其内联归属检查（保留 ValueError 文案与门禁时序：校验先于 gate 评估）。验证：既有 publish/门禁回归零改动通过
- [x] 1.4 `list_publications` 迁移到 `_ensure_access(..., minimum_role=VIEWER)`，删除 #276 的方法内联检查；调用签名从 `workspace_id`/`is_system_scope` 改为 `auth_context`。验证：`test_agent_publication_receipt.py` 全部断言零改动通过

## 2. API 层接线

- [x] 2.1 在 `src/hecate/studio/api/agent_versions.py` 新增 `_forbidden(exc)` helper（canonical error body，`code: FORBIDDEN`）；13 个端点全部传 `auth_context=ctx` 并捕获 `WorkspaceRoleRequiredError` → 403。验证：ruff/mypy 通过
- [x] 2.2 `list_agent_publications` 端点改传 `auth_context=ctx`（移除 `workspace_id`/`is_system_scope` 透传）。验证：该端点既有响应形状不变

## 3. 访问边界测试

- [x] 3.1 新增 `tests/test_api/test_agent_version_access_boundary.py`：自包含第二 workspace/member 构造（不改公共 conftest）；跨 workspace 13 端点矩阵——读返回 404 且响应与不存在 Agent 一致、写返回 404 且 W1 状态逐项不变（版本列表/指针/凭据）。验证：`python -m pytest tests/test_api/test_agent_version_access_boundary.py -q` 通过
- [x] 3.2 同文件补角色矩阵：同 workspace VIEWER 读全部成功、5 个写全部 403 且状态不变；EDITOR/ADMIN 写成功；缺 workspace 非 system 身份读 404/403、写拒绝；显式 system-scope 读写通行。验证：同上
- [x] 3.3 同文件补逐端点"API 必传身份"断言（每个端点以跨 workspace 身份请求断言 not found，防未来新端点/改造绕过）与 publish/publications 回归锚点（require 门禁阻断、force 留审计、跨 workspace 历史查询 404、读取不重算门禁照旧）。验证：同上
- [x] 3.4 回归收集：`python -m pytest tests/test_api/test_agent_versioning.py tests/test_api/test_agent_version_evaluation_binding.py tests/test_api/test_agent_publication_receipt.py -q` 预期零改动通过（默认 ADMIN 身份同 workspace）

## 4. 主 spec 结构修复（归档前置）

- [x] 4.1 删除 `openspec/specs/agent-versioning/spec.md` 第 126 行残留的 `## ADDED Requirements` 增量头及其后空行，使其后 10 条 requirement 回到 `## Requirements` 节；不改动任何 requirement/scenario 文本。验证：`openspec show agent-versioning --type spec` 仍列出全部 13 条 requirement（条目与修复前一致）；`openspec validate step11-agent-version-access-boundary --strict` 不再出现 archive 拒绝警告

## 5. 收尾验证

- [x] 5.1 共享门禁：`bash scripts/dev.sh check all`（ruff check + format + mypy strict）0 错误
- [x] 5.2 影响面测试：`bash scripts/dev.sh test tests/test_api/test_agent_version_access_boundary.py tests/test_api/test_agent_versioning.py tests/test_api/test_agent_version_evaluation_binding.py tests/test_api/test_agent_publication_receipt.py tests/test_channel -q` 全绿；如有跳过项逐项说明
- [x] 5.3 在 `verification.md` 登记结果与保留限制：版本生命周期之外的其他 studio/ops 端点存在同类授权缺口（如 evaluations、memory 等），属后续独立 change；本次未扩展
- [x] 5.4 `openspec validate step11-agent-version-access-boundary --strict` 通过；tasks.md 全部勾选


## 6. 审查修正

- [x] 6.1 恢复 list_publications 省略身份时的既有拒绝行为；测试先复现 DID NOT RAISE，再修正。既有生命周期方法的可信内部路径不变。
- [x] 6.2 补 viewer force 发布拒绝测试；拒绝前后快照核对完整版本内容及发布凭据，不仅核对数量；路由清单守护确保新增生命周期路由必须进入访问矩阵。
- [x] 6.3 重跑影响面与兼容面测试、共享检查及 OpenSpec strict validation，保存实际审查结果。
