# Verification

## 门禁结果

| 检查 | 命令 | 结果 |
|---|---|---|
| 共享检查 | `bash scripts/dev.sh check all` | ✅ ruff check（src/hecate + packages + tests）全部通过；ruff format 1606 文件已格式化；mypy 954 源文件 0 问题 |
| 影响面测试 | `bash scripts/dev.sh test tests/test_api/test_agent_version_access_boundary.py tests/test_api/test_agent_versioning.py tests/test_api/test_agent_version_evaluation_binding.py tests/test_api/test_agent_publication_receipt.py tests/test_channel -q` | ✅ 213 passed，0 failed，0 skipped（含 9 项新边界测试） |
| 兼容面补充 | `pytest tests/test_api/test_model_publishing.py tests/test_tools/test_skill_versioning.py -q` | ✅ 24 passed（其余直接构造 `AgentVersionService` 的调用方，可信内部路径零破坏） |
| OpenSpec | `openspec validate step11-agent-version-access-boundary --strict` | ✅ valid；主 spec 结构修复后 archive 拒绝警告消失 |

跳过项：无。本次复跑的 7 条 warnings 来自测试收集（导入 test_session_factory）、第三方库弃用（lark_oapi / websockets / uvicorn）及 Task API 既有 HTTP 422 常量；不应全部归因为 _map_service_error。相关文件未在本 change 修改。

## 覆盖说明

- **跨 workspace 13 端点矩阵**（读 8 返回 404 `NOT_FOUND` 且不泄露内容；写 5 返回 404 且状态逐项不变）：`test_cross_workspace_reads_surface_as_not_found`、`test_cross_workspace_writes_change_nothing`。这两条同时充当"API 必传身份"守护——若未来端点丢掉 `auth_context=ctx` 透传，跨 workspace 身份会拿到 200 而非 404，测试立即失败。
- **角色矩阵**：viewer 读 8 端点全 200 + 5 写全 403 且状态不变（`test_viewer_reads_own_workspace_and_cannot_write`）；同 workspace 无角色 403（`test_membership_without_role_is_forbidden`）；editor 五个写端到端成功（commit/update/publish/rollback/delete 各按语义断言）+ admin commit（`test_editor_and_admin_can_write`）。
- **身份边界**：缺 workspace 非 system 身份读+写全拒（`test_missing_workspace_identity_is_denied`）；显式 system-scope 读 8 + 写 2 通行（`test_system_scope_bypasses_boundary`）。
- **publish/publications 回归锚点**：require 门禁 409 `EVALUATION_GATE_BLOCKED`、force 落 `force_bypassed` 凭据、跨 workspace 历史查询 404、gate_off ≠ passed（`test_require_gate_block_and_force_receipt_survive_boundary`、`test_gate_off_receipt_outcome_unchanged`）。完整凭据语义（原子性、锚定、白名单）由既有 `test_agent_publication_receipt.py` 12 项回归承载，零改动通过。
- **主 spec 修复**（任务 4.1）：删除第 126 行残留 `## ADDED Requirements` 头，diff 仅 2 行删除；`openspec show agent-versioning --type spec` 仍列出全部 13 条 requirement（条目不变）。

## 保留限制（超出本切片）

1. **其他 studio/ops 端点的同类授权缺口未处理**：本次边界仅覆盖 Agent 版本生命周期的 13 个端点。 evaluations、memory、datasets 等其他 studio 资源端点若存在等效缺口，属后续独立 change（建议按资源域逐一收敛到 `ensure_workspace_role` + 归属校验的同一模式）。
2. **`auth_context=None` 仍是受信任内部路径**：既有生命周期方法省略身份的兼容路径保留（保持直接构造服务的调用方兼容）；发布历史查询维持既有缺失身份拒绝，内部读取也须显式携带身份。REST 面已由逐端点测试守护；未来新增非 HTTP 入口（MCP 工具等）应显式传入身份并复用 `_ensure_access`。
3. **测试基建既有约束**：单进程内 `db_session` 与 API 会话共享连接事务——边界拒绝引发的依赖 teardown 回滚会连带回滚未提交的种子数据，因此本测试文件在种子后显式 `commit`；新写类似边界测试时需沿用该模式。


## 独立审查与修正后复跑

- **[P2] 发布历史查询默认权限退化**：原 list_publications 缺失 workspace 身份即拒绝；本次共享 helper 的 None 兼容路径意外使省略身份变成全局读取。新增服务层测试先复现 DID NOT RAISE；现恢复 auth_context=None 时的 not found，内部查询快照显式传 system-scope。
- 拒绝前后同时比较完整版本 snapshot、发布指针及完整凭据；补 viewer force 发布仍 403，以及所有注册生命周期路由都进入身份/角色矩阵的守护。
- 完整影响面及兼容面：`.venv/Scripts/python.exe -m pytest tests/test_api/test_agent_version_access_boundary.py tests/test_api/test_agent_versioning.py tests/test_api/test_agent_version_evaluation_binding.py tests/test_api/test_agent_publication_receipt.py tests/test_channel tests/test_api/test_model_publishing.py tests/test_tools/test_skill_versioning.py -q` → **239 passed，7 warnings，无跳过**。
- 在该轮之后补充路由清单守护，最终边界测试单独复跑：`.venv/Scripts/python.exe -m pytest tests/test_api/test_agent_version_access_boundary.py -q` → **12 passed**。该轮与上述影响面有交集，不相加为独立测试总数。
- `.venv/Scripts/python.exe scripts/dev.py check all` → ruff check 通过、format 1606 files 通过、mypy 954 source files 无错误。新增路由守护测试随后单独通过 ruff check / format；未再修改生产代码。
- `openspec validate step11-agent-version-access-boundary --strict` 与 `git diff --check` 通过。
- 未提交、未推送、未归档。CI 全套及 PR 审查留待后续流程。
