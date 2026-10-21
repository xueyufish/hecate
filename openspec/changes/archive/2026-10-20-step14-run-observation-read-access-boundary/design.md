# Design: step14-run-observation-read-access-boundary

## 背景(已实证)

四个 Run 观察 GET 入口(`tasks.py`:390/399/408/428)只传 `_workspace_id(ctx)` + run_id,无角色判定。两处次序缺陷:

1. **SSE**:`stream_run_events` 先 `return StreamingResponse(event_source(), ...)`(HTTP 200 + SSE 头出站),资源校验在生成器内才发生,授权失败以 `event: error`(仅数字状态码)呈现。
2. **relay 次序**:`read_run_events`(task_control.py:610)在 `registry.get_run` 之前 `await self._pump_events()`——未授权请求先触发 outbox→事件日志投影(存储写入)。`stream_run_events` 本身没有直接 pump；其 entry 分支在迭代时调用 `read_run_events`。

既有构件(全部复用,不新增权限助手):

- `registry.get_run(run_id, workspace_id)`:run 行 `missing/foreign/deleted` 三态统一抛 `TaskNotFoundError`(→ 404 envelope)。
- `registry.get_task(task_id, workspace_id)`:父 Task 同款三态;RunModel 携带 `task_id`。
- **`list_run_actions` 已是 run+父 Task 双查先例**(:541:`get_run` + `get_task(run.task_id, ...)`,消息归一为 `run {run_id} not found in workspace`)——本设计将该模式推广到全部四个入口。
- `ensure_workspace_role` / `WorkspaceRoleRequiredError`(system scope 内部豁免、缺 workspace/role 抛错)。

## 权限门设计

**服务层**(task_control.py):

```python
async def assert_run_observable(self, *, workspace_id, run_id) -> RunModel:
    run = await self._registry.get_run(run_id, workspace_id)          # → TaskControlNotFoundError
    await self._registry.get_task(run.task_id, workspace_id)          # 父 Task 三态同判
    return run
```

错误映射与既有方法一致(`TaskNotFoundError` → `TaskControlNotFoundError`,404;message 沿用既有 `run {run_id} not found in workspace` 格式,仅回显调用方自带 run_id)。

**API 层**(tasks.py):新增模块级 `_run_read_gate(ctx, service, run_id) -> uuid.UUID`:

```python
workspace_id = _workspace_id(ctx)                       # 403 NO_WORKSPACE(现状保持,在前)
try:
    await service.assert_run_observable(workspace_id=workspace_id, run_id=run_id)
except Exception as exc:
    raise _map_service_error(exc) from exc              # 404
try:
    ensure_workspace_role(ctx, WorkspaceRole.VIEWER)
except WorkspaceRoleRequiredError as exc:
    raise _error("FORBIDDEN", exc.message, status.HTTP_403_FORBIDDEN) from exc
return workspace_id
```

四个入口开头调用 `_run_read_gate`,通过后按既有路径调用服务方法。**SSE** 的门在 `event_source` 定义与 `StreamingResponse(...)` 之前——被拒请求返回正常 HTTP 403/404,不建立流;生成器内的 `event: error` 帧仅保留给建立后的运行时异常(现状不变)。复用 #297 已引入的 `WorkspaceRole/WorkspaceRoleRequiredError/ensure_workspace_role` imports。

## relay 次序双保险

`read_run_events` 先经 `assert_run_observable` 校验 Run 与父 Task，再执行 `_pump_events()`。API 门先行拦截无角色等 REST 请求；服务层只负责资源可见性，不给可信内部调用新增角色参数。

`stream_run_events` 原本没有直接 pump；本轮改用同一可见性判定，entry 分支继续通过 `read_run_events` 读取，platform 分支维持原事件存储循环。`list_run_actions` 既有双查次序不变；其余 task 级方法的 pump 次序不在范围内。

## 关键语义

- **可见性先于角色**:三态不可见(run 删除/父 Task 删除或归属变化/不存在)统一 404,message 仅回显调用方自带 run_id(既有格式,回显非泄露);同 workspace 角色不足 403。次序与 #287/#297 一致。
- **成功路径零变化**:四入口通过门后走完全既有代码;VIEWER/EDITOR/ADMIN 响应结构、动作白名单、游标分页、SSE 终态与断线续读由既有回归承载。
- **身份规则**:`_workspace_id` 的 403 NO_WORKSPACE 在最前(现状保持);system-scope key 无 workspace → 同 403,不新增跨 workspace 读取;`ensure_workspace_role` 对 system scope 的豁免在此不可达(无 workspace 已被拒),行为与 #297 写门一致。
- **拒绝零副作用**:门在 relay/账本/事件存储/命令存储之前;被拒请求无 pump、无写入。

## 测试设计

新增 `tests/test_api/test_run_observation_access_boundary.py`,复用 `test_task_commands_query.py`/`test_task_command_access_boundary.py` 的 fixture 模式(agent/version/principal/deployment 种子 + registry 建任务/Run + SQL suite + auth holder 切换)。需要 Run 种子:`registry.create_run(...)`(部署/身份链复用已有种子)。

- **双 workspace**:W2 身份 × 四入口 → 404 且与不存在 run_id 的 error code 一致(message 仅回显自带 ID);W1 零副作用。
- **软删除/父 Task**:run.deleted、父 task.deleted、父 Task 归属变化(改 task.workspace_id 后)→ 均 404 与不存在一致。
- **角色**:viewer 四入口 200(详情/动作/事件分页 + SSE 建立断言 `media_type == "text/event-stream"`);role=None 403 FORBIDDEN。
- **SSE 时序**:不可见 run 的 stream 请求 → HTTP 404/403(非 200),无 `text/event-stream` 头,relay 计数(注入 fake relay 计数器)不变。
- **relay 次序**:viewer 正常读事件时 pump 发生在资源校验后(用计数器断言:不可见请求 pump 计数为 0)。
- **身份**:缺 workspace JWT → 403 NO_WORKSPACE;system key → 同;均零副作用。
- **回归**:既有 run 观察测试(`test_task_commands_query.py`、`test_task_control_api.py`、事件分页/SSE 相关文件以 grep 定位)全绿。

## 兼容性

| 场景 | 行为 |
|---|---|
| viewer/editor/admin 同 workspace 读取 | 与现状一致(门放行) |
| 跨 workspace/Run 软删除/不存在 | JSON 入口维持 404；SSE 改为建流前 HTTP 404 |
| 父 Task 删除或归属变化 | 详情/事件补齐 404；动作接口维持既有双查语义 |
| 同 workspace 无角色 | 从可读取变为 403 FORBIDDEN |
| 缺 workspace/system scope | 保持 403 NO_WORKSPACE |
| SSE 授权失败 | 从"200 + error 帧"变为正常 HTTP 403/404(本 change 目的) |
| 未授权请求触发 relay | 从"先 pump 后校验"变为"拒绝不 pump"(本 change 目的) |

## 非目标

- `GET /api/tasks/{task_id}/commands`、`GET /commands/{command_id}`、`GET /reconciliation/pending`、Task 详情读端点的角色门槛(相邻独立切片,proposal 已登记)。
- SSE 建立后的长连接动态撤权;task 级方法(:371/:381/:394)的 pump 次序;registry 全局语义、共享权限助手、评测模块、前端、Runtime、共享 skill 文件。
