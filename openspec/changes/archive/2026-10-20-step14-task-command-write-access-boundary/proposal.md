# Proposal: step14-task-command-write-access-boundary

## Why

Task 控制命令的两个 REST 写入口(`POST /api/tasks/{task_id}/commands`、`POST /api/tasks/{task_id}/cancel`,均经 `_issue` 汇入 `TaskControlService.issue_command`)只传 workspace 与签发者,服务层做 workspace 归属校验后即可记录命令并进入幂等重放与 outbox 投递——全链路没有 viewer/editor 角色检查。同 workspace 的 viewer 因此可请求取消、暂停、恢复或补充输入;且幂等重放先于任何角色判定,已降权用户可凭旧 `command_id` 绕过。这是 step14"最小干预接口"缺失的服务端写权限门槛,与 #283(Agent 版本生命周期)、#287(评测 Run 读取)、#289-#293(评测域)建立的边界模式同族。

## What Changes

- 两个 REST 写入口统一新增权限门:有效请求先校验 Task 存在、未删除且属于当前 workspace(跨 workspace/软删除/不存在一致返回 404),再要求调用方在当前 workspace 具备最低 `EDITOR` 角色(不足或缺失返回 403);门槛置于 `_issue`,两个入口与未来命令种类一并覆盖。
- 权限检查先于命令存储访问、幂等回放、后端调用及状态迁移;已降权用户的旧 `command_id` 重放同样被 403 拒绝,已有回执保持不变。
- 角色取自服务端 `AuthContext` 与既有 `ensure_workspace_role`,不接受请求体自报角色;保留既有 workspace 要求,不扩大 system scope 行为(缺 workspace 身份维持 403 `NO_WORKSPACE`,不新增跨 workspace 命令能力)。
- 原审批主体、wait token、`expected_revision`、期限、幂等键绑定与回执状态语义全部保持;EDITOR 只是新增最低门槛,不替代原审批校验。
- 拒绝时零副作用:不产生命令回执,Task/Run 状态、outbox 与后端调用全部不变。
- `TaskControlService` 服务签名保持兼容；`submit_workflow_callback` 会在内部调用 `issue_command`，该可信调用不经 REST 门，必须保留其既有行为。

**已知相邻缺口(本 change 不处理,显式登记)**:`POST /{task_id}/workflow-callback`(协议回调,自带 token 信任模型)、Task 提交入口(`POST /api/tasks`)、命令读取端点的 viewer 门槛(当前仅 workspace 作用域)——均为独立切片;登记于此避免"Task 访问边界已闭合"的误读。

## Capabilities

### New Capabilities

(无)

### Modified Capabilities

- `platform-task-control`:新增"任务控制命令携带最低写角色门槛"要求(仅 ADDED,不修改既有要求)。

## Impact

- 代码:`src/hecate/channel/api/tasks.py`(`_issue` 处加权限门,复用 `ensure_workspace_role` 与任务可见性判定);必要时 `src/hecate/execution/task_control.py` 小幅调整(如暴露可见性判定)。
- 测试:新增 `tests/test_api/test_task_command_access_boundary.py`(双 workspace/viewer/editor/admin/无角色/缺 workspace/软删除/降权重放/零副作用矩阵);回归既有 Task 命令相关测试(`tests/test_api/` 下 task 命令查询、回执与 workflow-callback 测试)。
- 规范:`openspec/specs/platform-task-control/spec.md` 经 delta 同步(仅 ADDED)。
- 无数据库迁移、无前端改动;不修改评测模块(与两个在途 step10 worktree 零交集)、Runtime、命令存储结构与共享 skill。
