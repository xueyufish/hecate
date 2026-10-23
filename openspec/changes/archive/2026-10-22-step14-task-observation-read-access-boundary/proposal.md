# Proposal: step14-task-observation-read-access-boundary

## Why

平台 Task 的三个观察 GET 入口(`GET /api/tasks`、`GET /api/tasks/{task_id}`、`GET /api/tasks/{task_id}/runs`)只做 workspace 作用域,没有最低 VIEWER 角色检查:有 workspace 但无角色的认证身份仍能读取任务目标、生命周期、等待信息与 Run 列表。这是 #297(命令写 EDITOR 门)与 #302(Run 观察 VIEWER 门)同族的读取边界缺口——#302 提案登记的相邻读端点缺口之一。

## What Changes

- `GET /api/tasks/{task_id}` 与 `GET /api/tasks/{task_id}/runs` 新增两级读权限门(与 #302 同构,先可见性后角色):有效 workspace → 复用 `assert_task_visible` 校验 Task 存在、未软删除、属于当前 workspace(跨 workspace/软删除/不存在一致 404,message 仅回显调用方自带的 task_id)→ 当前 workspace 至少 `VIEWER` 角色(不足或缺失 403);两者共用 `get_task_detail` 读取路径,门覆盖一致。
- `GET /api/tasks` 新增角色门:有效 workspace → 至少 `VIEWER`(该入口无单资源 ID,不涉及可见性 404)→ 才进入列表业务查询;分页、`lifecycle_state` 过滤与 `unrecorded` 投影语义不变。
- 角色取自服务端 `AuthContext` 与既有 `ensure_workspace_role`,不接受自报角色;缺 workspace 的非 system 身份维持 403 `NO_WORKSPACE`,system-scope 身份维持现状(不新增跨 workspace 读取)。
- 拒绝零副作用:权限门先于列表/详情业务查询、durable 状态读取、派发与 relay;被拒请求不产生任何存储写入。
- 服务签名与共享认证、Runtime、前端、评测模块、命令存储结构全部不变;`assert_task_visible` 为 #297 既有构件,仅复用。

**已知相邻缺口(本 change 不处理,更新 #302 登记清单)**:`GET /api/tasks/{task_id}/commands`、`GET /commands/{command_id}`、`GET /reconciliation/pending` 的读角色门槛;SSE 长连接动态撤权——均为独立切片。

## Capabilities

### New Capabilities

(无)

### Modified Capabilities

- `platform-task-control`:新增"Task 观察读取携带最低读角色门槛"要求(仅 ADDED,不修改既有要求)。

## Impact

- 代码:`src/hecate/channel/api/tasks.py`(三个 GET 入口加门;复用 #297 的 `assert_task_visible` 与 `_error`/FORBIDDEN 模式)。
- 测试:新增 `tests/test_api/test_task_observation_access_boundary.py`(独立夹具,不改动其他 worktree 的测试或公共 conftest);回归既有 Task API、命令写边界(#297)与 Run 观察边界(#302)测试。
- 规范:`openspec/specs/platform-task-control/spec.md` 经 delta 同步(仅 ADDED)。
- 无数据库迁移、无前端改动、不修改评测模块(与两个在途 step10 worktree 零交集)、Runtime 或共享 skill。
