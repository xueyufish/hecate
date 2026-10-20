# Design

## Context

#287/#290/#293/#295/#296 已覆盖 Run/Dataset/版本/样本;评测任务(EvaluationTask)三个读取接口([evaluation_tasks.py:81/102/205](../../../../src/hecate/ops/api/evaluation_tasks.py))仍无角色检查,且有两个特有缺陷:service 把 `workspace_id` 直接放进 `==` 条件(`tasks/service.py:177/199`),system 的 `None` 生成 `IS NULL` → 列表恒空、详情恒 404(system 读取目前完全不可用,是"读不到"而非"漏读");`list_task_runs`(`:384-403`)只按 `task_id + ~deleted` 过滤 Run,不约束 Run 自身 workspace。`get_task`/`_get_task_or_raise` 签名为非 Optional `workspace_id: uuid.UUID`,被 update/delete/enable/queue-run 写路径共用。

## Goals / Non-Goals

**Goals:**

- 三个读取接口获得与家族边界同构的角色地板与归属优先校验;404 沿用既有 "Evaluation task not found" envelope(`_not_found()`,恒定零 ID)。
- 修复 system scope 读取(可观察行为变化:从恒空/404 变为跨 workspace 可读)。
- Run 列表补 Run workspace 完整性条件,`items`/`total` 同条件。

**Non-Goals:**

- 不改 `get_task`/`_get_task_or_raise` 签名与语义;不改任务写接口、报告 API、平台 Task 控制、评分查询、Runner、Runtime、前端;不跨在途分支提取共享 helper。

## Decisions

### D1:`get_task`/`_get_task_or_raise` 零变化,system 详情走 API 层只读解析

新增 API 层 helper `_get_task_for_read(ctx, task_id, db) -> EvaluationTaskModel`:缺 workspace 的非 system 直接返回既有 `_not_found()`;其余身份按 `id + ~deleted` 只读查询 Task,非 system 再校验 workspace,system 不限制 workspace。未命中或跨 workspace 均返回相同恒定 404。helper 不改变供写路径共用的 service 查询。

- 关键理由:`get_task`/`_get_task_or_raise` 的 `== workspace_id` 语义被写路径依赖;若放宽签名为 Optional,system 写调用传 `None` 会从"任务不存在"变成"无作用域写"——本 change 明确不做。写路径行为(含 system 写仍 404 的现状)由回归断言钉死。
- 备选:改 service `get_task` 接受 Optional——被否决,理由如上。

### D2:仅 Task 列表允许 None,Run 列表始终接收已解析的真实 workspace

`list_tasks(workspace_id: uuid.UUID | None, ...)`:非 None 才追加 workspace 过滤。None 仅由 API 完成显式 system scope 判定后传入,普通缺 workspace 身份在角色门拒绝。

`list_task_runs(task_id, workspace_id: uuid.UUID, ...)` 保持原非 Optional 签名,API 将 `_get_task_for_read` 返回的 Task 的真实 workspace 传入,system 与普通身份一致。service 复用既有 `_get_task_or_raise`,Runs 查询增加 `EvaluationRunModel.workspace_id == task.workspace_id`,`items`/`total` 继续共用同一 base_query。避免在 service 内重复维护 system 无作用域 Task 解析分支。

API 保留原 `EvaluationTaskNotFoundError → _not_found()` 映射:Task 在边界校验与 service 查询之间被删除时仍为恒定 404,不会冒出内部异常。

### D3:三个端点的校验顺序

- 列表:角色 → 作用域化查询(#287 Run 列表同构;列表无路径资源,无归属前置)。
- 详情:Task 解析 → 角色(归属优先)。
- Runs:Task 解析 → 角色 → service(list 已在 service 内解析 Task,runs 用 Task 真实 workspace)。

### D4:测试镜像家族套件

新增 `tests/test_api/test_evaluation_task_access_boundary.py`,复用夹具模式;重点覆盖:双 workspace 列表、system 跨 workspace 列表/详情/Runs(修复验证:修正前 system 列表为空)、缺 workspace 拒绝、detail 404 三态同体零 ID、Runs 脏关联防御(构造 workspace 不匹配的 Run 行,断言不进 items/total)、归属优先于角色、角色地板、软删、分页/type/status 过滤保持、写路径行为不变断言(非 system 正常写 + system 写仍按现状 404,钉死 D1 不放宽)。复审补充:完整 404/403 envelope、低角色下软删除/缺失/跨 workspace Task 的详情与 Runs、system/普通身份的过滤后分页和总数、排序、summary、读取前后 Task/Run 全部持久列快照,以及 service NotFound 竞争的 404 映射回归。回归包括既有 Task API、全部 API 与 evaluation service。

## Risks / Trade-offs

- [system 读取从恒空变为可读是可观察变更] → 即本 change 的缺陷修复;spec 场景显式钉死。
- [service `list_tasks` 接受 None 后直调方语义变化] → 直调方(既有测试/内部调用)均传具体 workspace;None 语义在 docstring 显式声明为"跨 workspace(仅 system 读取路径)"。
- [Run workspace 条件可能排除历史脏数据] → 即完整性修复目的;spec 场景显式。

## Migration Plan

纯读取路径收紧与缺陷修复,无数据迁移、无契约版本变更。回退 = 代码回滚,无持久状态需要恢复。

## Open Questions

(无)
