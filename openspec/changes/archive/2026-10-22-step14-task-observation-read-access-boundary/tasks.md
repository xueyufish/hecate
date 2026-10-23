# Tasks: step14-task-observation-read-access-boundary

## 1. 读权限门(`src/hecate/channel/api/tasks.py`)

- [x] 1.1 新增 `_task_read_gate(ctx, service, task_id | None)`:`_workspace_id`(403 NO_WORKSPACE 前置)→ detail/runs 先 `assert_task_visible`(三态一致 404,message 仅回显自带 task_id)→ `ensure_workspace_role(ctx, WorkspaceRole.VIEWER)`(403 FORBIDDEN)。验证:模块可导入 + 最小用例。
- [x] 1.2 三入口接线:`list_tasks`(门,task_id=None)、`get_task`/`list_task_runs`(门带 task_id);通过后走完全既有读取路径。验证:既有 Task API 测试全绿 + 最小用例。

## 2. 边界测试(`tests/test_api/test_task_observation_access_boundary.py`,新增,独立夹具)

- [x] 2.1 拒绝矩阵:foreign identity / missing / soft-deleted × (detail, runs) → 404 且 error code 与不存在一致(message 仅回显自带 task_id);role=None × 三入口 → 403 FORBIDDEN;缺 workspace 与 system key → 403 NO_WORKSPACE(现状保持);跨 workspace 无角色 → 404(可见性先于角色)。验证:`pytest tests/test_api/test_task_observation_access_boundary.py -q --timeout=30` 对应用例通过。
- [x] 2.2 authorized 矩阵与语义保持:viewer/editor/admin × 三入口 200 且与 admin 基线逐响应一致;分页、`lifecycle_state` 过滤、`unrecorded`、详情等待信息、Run 排序断言保持。验证:对应用例通过。
- [x] 2.3 零副作用与伪造角色:被拒请求上 `list_tasks`/`get_task_detail` 调用计数为 0,durable 快照与 Task 行不变;query 伪造 `role=admin` 不提权。验证:对应用例通过。

## 3. 回归与共享检查

- [x] 3.1 关联回归:`pytest tests/test_api/test_task_commands_query.py tests/test_api/test_task_command_access_boundary.py tests/test_api/test_run_observation_access_boundary.py tests/test_channel/test_task_control_api.py tests/test_execution/test_task_control_service.py -q` 全绿;如既有测试以无角色身份做 Task 观察,按新契约补角色并逐条记录,不允许批量放宽。
- [x] 3.2 共享检查:`bash scripts/dev.sh check all` 通过;`openspec validate step14-task-observation-read-access-boundary --strict` 通过;`python scripts/feature_inventory.py check` 无新增漂移。

## 4. 收尾

- [x] 4.1 在 `verification.md` 记录基点(origin/main `a8b1aeed`)、实际测试命令与结果、delta 场景与测试的对应关系、跳过范围及原因。
- [x] 4.2 勾选完成项,自查 diff 仅触及 `tasks.py`、新增测试文件与本 change artifacts;确认未触及 service/共享认证/Runtime/前端/评测模块、command 写入口(#297)、Run 观察门(#302)与共享 skill 文件。
