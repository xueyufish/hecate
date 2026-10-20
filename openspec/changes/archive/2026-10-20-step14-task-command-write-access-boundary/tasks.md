# Tasks: step14-task-command-write-access-boundary

## 1. 权限门(`src/hecate/channel/api/tasks.py`,必要时 `src/hecate/execution/task_control.py`)

- [x] 1.1 `_issue` 入口加两级权限门:任务可见性(存在 + 未软删除 + 当前 workspace,三种不可见情形统一 404 envelope)→ `ensure_workspace_role(ctx, WorkspaceRole.EDITOR)`(403 映射复用既有模式);门槛置于幂等回放/命令存储/outbox 之前;`_workspace_id` 的 403 NO_WORKSPACE 保持在前。验证:`python -m pytest tests/test_api/test_task_command_access_boundary.py -q`(先落最小用例)。
- [x] 1.2 软删除处理:核对 `registry.get_task` 是否过滤软删除,未过滤则在门内补 `~deleted` 判定,保证三态一致 404。验证:软删除用例通过。
- [x] 1.3 服务签名兼容:确认 `TaskControlService.issue_command` 无需签名变更(可信内部调用不经 REST 门);如有小幅调整仅限暴露可见性判定,不改变行为。验证:既有 task 相关测试全绿。

## 2. 边界测试(`tests/test_api/test_task_command_access_boundary.py`,新增)

- [x] 2.1 双 workspace 与软删除:W2 成员对 W1 Task 的 commands/cancel 均 404 且与不存在 Task 响应体一致;deleted Task 同 404;W1 内命令回执/Task/Run/outbox/后端调用零变化。验证:`pytest tests/test_api/test_task_command_access_boundary.py -q` 对应用例通过。
- [x] 2.2 角色矩阵:viewer 两入口 403;editor/admin 两入口按既有语义成功(REQUESTED 回执);role=None 同 workspace 403;缺 workspace 非 system 身份 403 NO_WORKSPACE(现状保持);显式 system key 维持既有行为不新增能力。验证:对应用例通过。
- [x] 2.3 降权重放与零副作用:EDITOR 下发成功 → 降权 viewer → 同 `command_id` 同请求体重发 403 且既有回执逐字段不变;全部被拒请求前后命令存储/Task/Run/outbox/后端调用计数不变。验证:对应用例通过。
- [x] 2.4 语义锚点:editor 对 `expected_revision` 冲突或需审批的请求仍按既有拒绝语义(门槛不替代审批);幂等重放对 editor 保持既有行为。验证:对应用例通过。

## 3. 回归与共享检查

- [x] 3.1 关联回归:`pytest tests/test_api/ -q` 中 task 命令查询/回执/workflow-callback/task 提交相关既有测试全绿(以 grep 定位实际文件);如既有测试以 viewer 身份下发命令,按新契约改为 editor 并逐条记录,不允许批量放宽。
- [x] 3.2 共享检查:`bash scripts/dev.sh check all` 通过;`openspec validate step14-task-command-write-access-boundary --strict` 通过;`python scripts/feature_inventory.py check` 无新增漂移。

## 4. 收尾

- [x] 4.1 在 `verification.md` 记录基点(origin/main `4f97d289`)、实际测试命令与结果、delta 场景与测试的对应关系、跳过范围及原因;确认降权重放与零副作用断言齐备。
- [x] 4.2 勾选完成项,自查 diff 仅触及 `tasks.py`(必要时 `task_control.py` 小幅调整)、新增测试文件与本 change artifacts;确认未触及 workflow-callback 语义、Task 提交、读取端点角色门槛、评测模块、前端、Runtime 与共享 skill 文件。

## 审查补记

审查期间，外部进程将本 change 归档、同步主规范、rebase 到 `origin/main` `d2e91a23` 并创建提交 `3b0fee0e`；审查修正的主体测试已被该提交包含。确认 GLM 停止后，补充 REQUESTED 回放用例及最终验证记录。归档后的严格校验采用归档内容的临时隔离副本与已同步主规范；任务完成校验针对当前归档。实际命令、结果与跳过项见 `verification.md`。本次审查没有执行 commit、push 或 archive。
