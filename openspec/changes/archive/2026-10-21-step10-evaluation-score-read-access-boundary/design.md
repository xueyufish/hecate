# Design

## Context

#287/#290/#293/#295/#296/#299/#300/#302 之后,`GET /api/evaluation/scores`([evaluation_tasks.py:254](../../../../src/hecate/ops/api/evaluation_tasks.py))尚未接入分数读取授权边界:无角色检查,`ctx.workspace_id` 直接传给 `svc.list_scores`;service 无条件 `workspace_id == workspace_id`(`tasks/service.py:424`)——system 的 `None` 生成 `IS NULL` → 恒空;带 `task_id` 查询不可见 Task 时,workspace 条件排除分数行 → 200 空列表(缺少 Task 可见性解析,未采用既有资源型查询的 404 契约)。service 签名 `list_scores(workspace_id: uuid.UUID, ...)` 为非 Optional,查询条件已含 `~deleted` 与全部既有过滤,排序 `created_at.asc()`。同文件既有 `_get_task_for_read(ctx, task_id, db)`(#299)与 `_forbidden` 可复用;#300 的 `NO_WORKSPACE` 403 形状已在 `evaluation.py`,本次直接导入复用。

## Goals / Non-Goals

**Goals:**

- 无 `task_id`:workspace 范围查询 + `NO_WORKSPACE`(含 system)+ viewer 地板。
- 带 `task_id`:`_get_task_for_read` 归属优先解析 + viewer 地板 + 以 Task 真实 workspace 查询。
- 查询语义、过滤、排序、人工标注兼容、disabled Task 可读全部保持;service 零变化。

**Non-Goals:**

- 不改 `svc.list_scores` 签名与查询、共享 helper 签名、Task 写接口、报告接口(`evaluation_reports.py` 归并行 change)、人工标注接口、前端、Runtime;不新增全局分数查询或 workspace 选择参数。

## Decisions

### D1:无 `task_id` → workspace 范围查询 + `NO_WORKSPACE` 先行

顺序:`ctx.workspace_id is None → 403 NO_WORKSPACE`(所有身份含 system,先于角色)→ `ensure_workspace_role(ctx, VIEWER)` → `_forbidden` → `svc.list_scores(workspace_id=ctx.workspace_id, ...)`。分数(含 task_id=NULL 的人工标注)属于具体 workspace,本边界不提供跨 workspace 全局分数列表——system 无 workspace 时不能使用无 `task_id` 形式,与其带 `task_id` 的跨 workspace 读取形成与 Dataset 创建契约(#300)相同的不对称。`NO_WORKSPACE` 直接复用已导入模块 `evaluation.py` 的 `_no_workspace`,与 #300 保持相同恒定 envelope;不新增 helper,也不引入对并行修改的 `evaluation_reports.py` 的依赖。

- 备选:system 无 workspace 时允许全局查询——被否决:分数列表无 Task 上下文,跨 workspace 全局列表是新的资源语义,超出"补齐读取授权"的切片边界。

### D2:带 `task_id` → 复用 `_get_task_for_read`,service 接收 Task 真实 workspace

顺序:`_get_task_for_read(ctx, task_id, db)`(Task 归属解析,404 恒定无 ID,非 system 缺 workspace 在其查询前返回)→ `ensure_workspace_role(ctx, VIEWER)` → `svc.list_scores(workspace_id=task.workspace_id, task_id=task_id, ...)`。以解析后 Task 的真实 workspace 作为查询条件,保证分数行与 Task 同 workspace;非 system 时该值即 `ctx.workspace_id`(归属已校验),system 时为 Task 自身 workspace。

- 备选:service `list_scores` 接受 Optional workspace——被否决:API 保证传具体值后签名无需变化,service 面保持零。
- 资源可见性对齐:不可见 Task → 404(与 #287 Run scores 的 "SHALL NOT return an empty score list" 家族语义对齐)。

### D3:查询语义保持的实现边界

本切片不改 `svc.list_scores`:`task_id=NULL` 人工标注行在省略 `task_id` 时自然返回(条件仅在非 None 时追加)、`source=human` 经既有 source 条件、disabled Task 的分数无生命周期过滤(禁用只停止采样)、排序/分页/其余过滤全部原样。端点仅新增边界检查与 workspace 实参来源变化。

### D4:测试镜像家族套件

新增 `tests/test_api/test_evaluation_score_access_boundary.py`,使用既有 db_session/client 夹具并直接创建 Task/Score 数据,读取测试无需 evaluator registry;重点:双 workspace 隔离、`NO_WORKSPACE` 含 system、viewer 正常读取/无角色 403、不可见 Task 三态同体 404 零 ID、system 带 task_id 跨 workspace 真实结果(修复验证)、人工标注(`task_id=NULL` + `source=human`)兼容、disabled Task 可读、分页/过滤/升序保持、表直查全列快照零副作用。回归:`test_evaluation_tasks_api.py` + `tests/test_api` 全量。

## Risks / Trade-offs

- [system 无 workspace 且无 task_id 查询从恒空变为 403] → 有意收紧错误契约;spec 场景显式钉死。
- [带 task_id 的不可见查询从 200 空列表变为 404] → 家族语义对齐;这是有意的错误契约变更;合法且没有匹配分数的可见 Task 仍返回 200 空列表,测试分别覆盖。
- [score 行的 workspace 字段为历史数据的场景] → 查询约束不变(仍按 workspace 过滤);本切片不回填历史行。

## Migration Plan

纯读取路径收紧,无数据迁移、无契约版本变更。回退 = 代码回滚,无持久状态需要恢复。

## Open Questions

(无)
