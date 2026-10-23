# Design: step14-task-observation-read-access-boundary

## 背景(已实证)

三个 Task 观察 GET 入口(`tasks.py`:208/226/380)只传 `_workspace_id(ctx)`:

- `list_tasks`(:208)→ `service.list_tasks`:workspace 作用域 + `deleted.is_(False)` 过滤,无角色;
- `get_task`(:226)与 `list_task_runs`(:380)→ `service.get_task_detail`:`_get_task` 做三态可见性(404,经 registry `get_task` 的 `missing/foreign/deleted` 统一语义),无角色。

可复用构件(#297/#302 已就位):`assert_task_visible`(task_control.py:1425,即 `_get_task` 的公开包装)、`ensure_workspace_role`/`WorkspaceRoleRequiredError`、`_error` 的 FORBIDDEN/NO_WORKSPACE envelope 模式。

## 权限门设计(tasks.py)

新增模块级门(与 `_run_read_gate` 同族):

```python
async def _task_read_gate(ctx: AuthContext, service: TaskControlService, task_id: uuid.UUID | None) -> uuid.UUID:
    workspace_id = _workspace_id(ctx)                     # 403 NO_WORKSPACE(现状保持,在前)
    if task_id is not None:                               # detail / runs:可见性先于角色
        try:
            await service.assert_task_visible(workspace_id=workspace_id, task_id=task_id)
        except Exception as exc:                          # noqa: BLE001 — _map_service_error → 404
            raise _map_service_error(exc) from exc
    try:                                                  # 列表/详情/runs 共用 VIEWER 下限
        ensure_workspace_role(ctx, WorkspaceRole.VIEWER)
    except WorkspaceRoleRequiredError as exc:
        raise _error("FORBIDDEN", exc.message, status.HTTP_403_FORBIDDEN) from exc
    return workspace_id
```

(`task_id=None` 即列表入口形态,避免两个门。)

接线:

- `list_tasks`:`workspace_id = await _task_read_gate(ctx, service, None)` → 既有 `service.list_tasks(...)`。
- `get_task` / `list_task_runs`:`workspace_id = await _task_read_gate(ctx, service, task_id)` → 既有 `service.get_task_detail(workspace_id, task_id)`(`list_task_runs` 继续从 detail 取 `runs`)。

404 envelope 沿用 `_map_service_error`(`TaskControlNotFoundError` → `NOT_FOUND`,message 仅回显自带 task_id);403 复用 FORBIDDEN envelope。`_workspace_id` 的 NO_WORKSPACE 在最前——system-scope key 无 workspace,维持现状 403,不新增跨 workspace 读取。

## 关键语义

- **可见性先于角色**(detail/runs);列表无资源 ID,只做角色门。
- **门先于业务读取**:`list_tasks`/`get_task_detail` 在门通过后才被调用——被拒请求不进入业务查询、不读 durable 状态、不触 relay(三者本入口也无 pump)。
- **成功路径零变化**:三入口通过门后走完全既有代码;分页/状态过滤/`unrecorded`/等待信息/Run 排序由既有回归承载。
- **服务签名零变化**:`assert_task_visible` 为 #297 既有公开方法;无新 service 能力、无迁移。

## 测试设计

新增 `tests/test_api/test_task_observation_access_boundary.py`,独立夹具(复制 `test_task_commands_query.py` 的 agent/version/principal/deployment 种子 + `registry.create_task` + SQL suite + auth holder 模式;不自改公共 conftest):

- **拒绝矩阵**:foreign identity(读 W1 task)、missing task、soft-deleted task × (detail, runs) → 404 且 error code 与不存在一致(message 仅回显自带 task_id);role=None、缺 workspace、system key × 三入口 → 403(FORBIDDEN / NO_WORKSPACE / NO_WORKSPACE)。
- **authorized**:viewer/editor/admin 三角色 × 三入口 200,只切换身份且读取辅助函数不重置身份,与 admin 基线逐响应比较;种入 waiting_input/queued 状态及 unrecorded Task、等待信息、多个 Run,验证分页/状态过滤及 attempt 排序,排除软删除/跨 workspace 数据并保持空 Run 列表。
- **跨 workspace 无角色**:W2 的 role=None 身份读 W1 task → 404(可见性先于角色)。
- **伪造角色**:请求体/query 携带 `role=admin` 的 GET → 仍按服务端身份判定(GET 无请求体,以 query 参数伪造断言)。
- **拒绝零副作用**:观察 `list_tasks`/`get_task_detail`、durable 读取/命令写入、`_pump_events`/`_dispatch_task` 与 counting relay,被拒请求调用列表为空;直接 SQL 查询 Task/Run/平台事件及全部 durable 表,对有实际种子数据的全列快照比较。缺 workspace 时另断言不进行 `assert_task_visible` 资源查找。
- **回归**:既有 Task API、#297 命令写边界、#302 Run 观察边界测试全绿。

## 兼容性

| 场景 | 行为 |
|---|---|
| viewer/editor/admin 观察 | 与现状一致(门放行) |
| 有 workspace 无角色 | 从可读变为 403 FORBIDDEN |
| 缺 workspace JWT / system key | 保持 403 NO_WORKSPACE |
| 跨 workspace/软删除/不存在 detail·runs | 统一 404(message 仅回显自带 ID) |
| 列表分页/过滤/投影、详情结构、Run 排序 | 不变 |

## 非目标

- `GET /{task_id}/commands`、`GET /commands/{command_id}`、`GET /reconciliation/pending` 的读角色门槛、SSE 动态撤权(相邻独立切片,proposal 已更新登记)。
- Task 提交/命令写入口(#297 已覆盖)、workflow-callback、评测模块、前端、Runtime、共享 skill、registry 全局语义。
