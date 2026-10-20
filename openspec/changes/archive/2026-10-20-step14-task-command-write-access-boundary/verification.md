# Verification

## 基点

`origin/main` `4f97d289`(#294),分支 `feat/step14-task-command-write-access-boundary`,worktree `.worktrees/step14-task-command-write-access-boundary`。改动文件:`src/hecate/channel/api/tasks.py`(imports + `_issue` 权限门)、`src/hecate/execution/task_control.py`(+`assert_task_visible` 公开判定)、`tests/test_api/test_task_command_access_boundary.py`(新增)、本 change artifacts。既有测试断言零修改。

## 门禁结果

| 检查 | 命令 | 结果 |
|---|---|---|
| 新边界测试 | `pytest tests/test_api/test_task_command_access_boundary.py -q` | ✅ 11 passed |
| 关联回归 | `pytest tests/test_api/test_task_commands_query.py tests/test_channel/test_workflow_callback_api.py tests/test_channel/test_task_control_api.py tests/test_execution/test_command_list_query.py -q` | ✅ 32 passed,0 failed |
| 合并回归(格式化后复跑) | 上述全部合并执行 | ✅ 43 passed,4 warnings |
| 共享检查 | `bash scripts/dev.sh check all` | ✅ ruff check 通过(修正 2 处 F401、1 处 F841 后);ruff format 1621 文件(测试文件经一次 format);mypy 962 源文件 0 问题 |
| OpenSpec | `openspec validate step14-task-command-write-access-boundary --strict` | ✅ valid |
| 规划数据 | `python scripts/feature_inventory.py check` | ✅ 0 errors;189 warnings 为既有登记债,本 change 未触及 feature YAML |

实现过程中的修正均为新测试代码问题(异步 `session.get` 补 await、`list_task_commands` 的 `(items, total)` 元组解包、`ControlCommandRecord` 属性访问、`expire`→`refresh`),无门行为影响。

## 实现要点

1. **门槛位置**:两入口经 `_issue` 汇聚,门置于 `_issue` 内——payload 形状校验(422,原位)→ `service.assert_task_visible`(新增公开判定,内部复用 `_get_task`/registry 的三态不可见语义)→ `ensure_workspace_role(ctx, WorkspaceRole.EDITOR)` → 既有 `issue_command`(幂等重放/记录/outbox/后端调用原样)。`_workspace_id` 的 403 `NO_WORKSPACE` 保持在前。
2. **软删除**:核对 `TaskRunRegistry.get_task`(:173)——`missing/foreign/deleted` 三态已统一抛 `TaskNotFoundError`,无需补判定;服务层经既有 `TaskControlNotFoundError` → 404 envelope 映射。404 message 回显调用方自带的 UUID 为既有格式(#283 先例:断言 error code 一致而非全 body 相等——回显不是存在性泄露,因为两情形消息同构且 ID 来自调用方)。
3. **取消入口状态码**:`POST /{id}/cancel` 路由无 `status_code=201` 覆盖,成功路径返回 200(与 `/commands` 的 201 并存,均为既有行为,门槛不改变)。

## 覆盖说明(delta 场景 ↔ 测试)

- **跨 workspace 一致 404**:`test_cross_workspace_commands_surface_as_not_found`——两入口均 404、error code 与不存在一致、message 仅回显调用方自带 ID、零回执。
- **软删除一致**:`test_soft_deleted_task_matches_missing`。
- **viewer 两入口 403 + 零副作用**:`test_viewer_is_forbidden_on_both_entries`(FORBIDDEN envelope,回执快照不变)。
- **editor/admin 正常下发**:`test_editor_and_admin_issue_commands`(cancel 200、commands 201、REQUESTED 回执)。
- **无角色**:`test_membership_without_role_is_forbidden`;**缺 workspace**:`test_missing_workspace_identity_keeps_existing_rejection`(NO_WORKSPACE 现状保持);**system scope**:`test_system_scope_keeps_existing_rejection`(bootstrap key 不新增跨 workspace 命令能力)。
- **降权重放**:`test_downgraded_user_cannot_replay_old_command_id`——EDITOR 下发成功 → 降为 viewer → 同 `command_id` 同请求体重发 403,既有回执逐条不变(角色门先于幂等回放)。
- **拒绝零副作用**:`test_rejected_requests_leave_task_and_outbox_untouched`——404/403 后 Task 行(refresh 后逐字段)、命令快照、durable task state 全部不变。
- **语义锚点**:`test_editor_idempotent_replay_still_reuses_receipt`(authorized 重放幂等保持)、`test_editor_still_honors_revision_and_expiry_semantics`(`expected_revision` 冲突 → rejected、过期 → expired,门槛不豁免既有校验)。

## 验证范围与保留限制

1. **未复跑**:全仓 pytest(本地门禁不含,CI 为准)、真实 PostgreSQL 端到端;命令存储为 `SqlDurableStore` 进程内 SQLite,与既有 task 命令测试同级。
2. **相邻缺口未处理**(proposal 已登记):`POST /{task_id}/workflow-callback`(协议回调 token 信任模型)、Task 提交入口(`POST /api/tasks`)、命令读取端点的 viewer 门槛——`workflow-callback` 与读取端点行为在本 change 下零变化(回归 32 项承载)。
3. **角色快照语义**:测试以 dependency override 切换身份模拟"降权";生产中降权经 workspace membership 变更生效,门只读请求时解析的 `AuthContext`,不引入额外缓存——两者一致性由既有认证层语义保证,不在本切片验证范围。
