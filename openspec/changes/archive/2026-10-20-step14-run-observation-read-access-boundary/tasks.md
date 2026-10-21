# Tasks: step14-run-observation-read-access-boundary

## 1. 服务层可见性与 pump 次序(`src/hecate/execution/task_control.py`)

- [x] 1.1 新增 `assert_run_observable(workspace_id, run_id) -> RunModel`:`registry.get_run` + `registry.get_task(run.task_id, workspace_id)` 双查(run/父 Task 三态不可见统一 `TaskControlNotFoundError`),错误映射与既有方法一致。验证:模块可导入 + 最小用例。
- [x] 1.2 `read_run_events`/`stream_run_events`:资源校验改经 `assert_run_observable`,`_pump_events()` 移到校验之后(拒绝请求不触发 relay);返回结构与分页/SSE 语义不变。验证:既有事件分页/SSE 测试全绿 + relay 计数用例。

## 2. 读权限门(`src/hecate/channel/api/tasks.py`)

- [x] 2.1 新增 `_run_read_gate(ctx, service, run_id)`:`_workspace_id`(403 NO_WORKSPACE 前置)→ `assert_run_observable`(统一 404,message 仅回显自带 run_id)→ `ensure_workspace_role(ctx, WorkspaceRole.VIEWER)`(403 FORBIDDEN);复用 #297 已有 imports。验证:最小用例通过。
- [x] 2.2 四个 GET 入口(`/{run_id}`、`/actions`、`/events`、`/events/stream`)开头调用门;SSE 的门在 `StreamingResponse` 之前(被拒返回正常 HTTP 403/404,不建立流)。验证:四入口用例通过。

## 3. 边界测试(`tests/test_api/test_run_observation_access_boundary.py`,新增)

- [x] 3.1 双 workspace 与删除矩阵:W2 身份 × 四入口 → 404 且 error code 与不存在一致(message 仅回显自带 ID);run.deleted、父 task.deleted、父 Task 归属变化 → 同 404;W1 零副作用(账本/事件/outbox/状态不变)。验证:`pytest tests/test_api/test_run_observation_access_boundary.py -q` 对应用例通过。
- [x] 3.2 角色矩阵:viewer 四入口 200/流建立成功(响应结构、动作白名单、游标分页、SSE 终态不变);role=None 403 FORBIDDEN;editor/admin 同 viewer(读无更高要求)。验证:对应用例通过。
- [x] 3.3 SSE 时序与 relay 次序:不可见/无角色的 `events/stream` → HTTP 404/403、无 `text/event-stream` 头、无 SSE 事件帧、relay 计数为 0;viewer 正常读取时 relay 在校验后发生。验证:对应用例通过。
- [x] 3.4 身份规则:缺 workspace JWT 与 system key → 403 NO_WORKSPACE(现状保持)、零副作用。验证:对应用例通过。

## 4. 回归与共享检查

- [x] 4.1 关联回归:`pytest tests/test_api/test_task_commands_query.py tests/test_channel/test_task_control_api.py` 及事件分页/SSE 相关既有测试(以 grep 定位)全绿;如既有测试以无角色身份读取,按新契约补角色并逐条记录,不允许批量放宽。
- [x] 4.2 共享检查:`bash scripts/dev.sh check all` 通过;`openspec validate step14-run-observation-read-access-boundary --strict` 通过;`python scripts/feature_inventory.py check` 无新增漂移。

## 5. 收尾

- [x] 5.1 在 `verification.md` 记录基点(origin/main `d061446f`)、实际测试命令与结果、delta 场景与测试的对应关系、跳过范围及原因。
- [x] 5.2 勾选完成项,自查 diff 仅触及 `tasks.py`、`task_control.py`(pump 次序 + 可见性判定)、新增测试文件与本 change artifacts;确认未触及共享权限助手、registry 全局语义、评测模块、前端、Runtime 与共享 skill 文件。

## 审查补记

补齐 36 项四入口拒绝矩阵、3 项角色成功路径、24 项内部服务资源拒绝路径，以及 entry 事件与建立后 SSE 错误帧各一项，最终专项为 65 项。关联回归共 227 项通过，具体命令、告警调查和限制见 `verification.md`。辅助方法复用既有 `TaskControlService` 类型标注；没有修改 registry、共享角色助手或已有测试。
