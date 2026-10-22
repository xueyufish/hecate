# Proposal: step14-run-observation-read-access-boundary

## Why

Run 观察接口的四个 GET 入口(`GET /api/runs/{run_id}`、`/{run_id}/actions`、`/{run_id}/events`、`/{run_id}/events/stream`)只做 workspace 作用域,没有最低 VIEWER 角色检查——与 #297 已建立的控制命令 EDITOR 写门不对称。且存在两处检查次序缺陷:SSE 入口在 `StreamingResponse` 返回(HTTP 200 + SSE 头已发出)之后才在生成器内做资源校验,授权失败以 `event: error` 帧呈现;`read_run_events` 服务方法在 `registry.get_run` 资源校验**之前**执行 `_pump_events()`——未授权请求会先触发 outbox→事件日志的 relay 投影(一次存储写入)。这是 step14 服务端边界(写门 #297 之后)的读取切片。

## What Changes

- 四个 GET 入口统一新增两级读权限门(与 #297 写门同构,先可见性后角色):有效 workspace → Run 及其父 Task 均存在、未软删除、属于当前 workspace(跨 workspace/软删除/不存在一致 404,message 仅回显调用方自带的 run_id)→ 当前 workspace 至少 `VIEWER` 角色(不足或缺失 403)。
- SSE 入口的权限门在创建 `StreamingResponse` **之前**完成:拒绝返回正常 HTTP 403/404,不再"200 后发送错误事件";生成器内的 `event: error` 帧仅保留给建立后的运行时异常(现状行为,授权失败不再走该路径)。
- `read_run_events` 的 `_pump_events()` 移到 Run 与父 Task 可见性校验**之后**；`stream_run_events` 复用同一可见性判定，但不新增 pump。内部事件读取调用方的不可见资源拒绝也不触发 relay；VIEWER 角色门仅属于本 change 的 REST 入口。
- 角色取自服务端 `AuthContext` 与既有 `ensure_workspace_role`,不接受任何自报角色;缺 workspace 的非 system 身份维持 403 `NO_WORKSPACE`,system-scope 身份维持现状(不新增跨 workspace 读取)。
- VIEWER/EDITOR/ADMIN 的成功响应、动作字段白名单、游标分页、SSE 终态与断线续读语义全部保持;拒绝零副作用:不触发 relay、无状态变化、无 outbox 写入。

**已知相邻缺口(本 change 不处理,显式登记)**:`GET /api/tasks/{task_id}/commands`、`GET /commands/{command_id}`、`GET /reconciliation/pending` 等其余读端点仍仅 workspace 作用域;SSE 建立后的长连接动态撤权不在本轮;均为独立切片。

## Capabilities

### New Capabilities

(无)

### Modified Capabilities

- `platform-task-control`:新增"Run 观察读取携带最低读角色门槛"要求(仅 ADDED,不修改既有要求)。

## Impact

- 代码:`src/hecate/channel/api/tasks.py`(四个 GET 入口加门,SSE 门前置);`src/hecate/execution/task_control.py`(`read_run_events` 的 pump 次序调整、`stream_run_events` 的父 Task 可见性检查及共同可见性判定)。
- 测试:新增 `tests/test_api/test_run_observation_access_boundary.py`(四入口 × 角色矩阵/跨 workspace/软删除/父 Task 删除或归属变化/缺角色/缺 workspace/system scope/拒绝零副作用);回归既有 Run 观察相关测试(`test_task_commands_query.py`、`test_task_control_api.py`、事件分页与 SSE 测试)。
- 规范:`openspec/specs/platform-task-control/spec.md` 经 delta 同步(仅 ADDED)。
- 不修改共享权限助手、registry 全局语义、评测模块(与在途 `step10-evaluation-report-aggregate-access-boundary` 零交集)、前端、Runtime、命令存储结构或共享 skill。
