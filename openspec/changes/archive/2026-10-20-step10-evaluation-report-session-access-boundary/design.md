# Design

## Context

`evaluation_reports.py` 的 `sessions_report`把 `ctx.workspace_id` 直传 `EvaluationReportService.session_rollup`:无角色检查、无 Task 解析;非 system 无 workspace 身份传 `None`(过滤隐患),system 身份传 `None` 得到空 rollup 而非目标数据。同文件已有全部所需助手(#298 distributions 与 #301 aggregates 两个切片铺设):`_require_aggregate_viewer`(聚合门:无 workspace → 403 `NO_WORKSPACE` 查询前拒绝;角色不足 → 403 `FORBIDDEN`)、`_get_visible_task_or_404`(Task 归属解析:缺失/已删除/跨 workspace → 无回显 404)、`_report_scope_not_found`、`_forbidden`。这是报告五入口边界的最后一个缺口。

## Goals / Non-Goals

**Goals:**

- sessions 按 `task_id` 有无分双模式接入:无 `task_id` 复用聚合门;带 `task_id` 走"查询前缺 workspace 拒绝 → Task 解析 → viewer 下限 → 服务用 Task 真实 workspace"。
- 零新增助手:全部组合既有函数。

**Non-Goals:**

- 不改报告服务、其余四个入口(均已边界化)、评测 Task CRUD、平台 Task、前端、Runtime;不新增 system 全局聚合能力。

## Decisions

1. **双模式分支置于端点首行,组合既有助手,零新增解析器。** `task_id is None` → `_require_aggregate_viewer(ctx)` + 服务用 `ctx.workspace_id`(该助手已保证非 None);带 `task_id` → 先显式判 `not ctx.is_system_scope and ctx.workspace_id is None` → `_report_scope_not_found()`(查询前拒绝,与 distributions 的"denied before resource lookup"语义一致)→ `_get_visible_task_or_404`(同体 404)→ `ensure_workspace_role(ctx, VIEWER)` 失败 `_forbidden` → 服务用 `task.workspace_id`。备选"给 `_require_aggregate_viewer` 加 task 参数做成通用门"被否决:两种模式的错误契约不同(403 `NO_WORKSPACE` vs 404),强合并会让助手携带分支语义。

2. **带 `task_id` 的缺 workspace 拒绝形状为 404 而非 403 `NO_WORKSPACE`**,与 distributions 的资源级语义一致:请求指向一个具体资源,不可见即 404,不向低权身份泄露"你缺 workspace"之外的差异;查询前显式拒绝避免用 `None` 过滤扫表。

3. **system 行为变更:Task 域内真实 rollup。** 与 distributions 相同的修复——`None` 过滤下的空结果不是数据;system 的跨 workspace 能力经由资源归属表达,不引入全局聚合。

## Risks / Trade-offs

- [两种模式错误形状不同(无 task_id 缺 workspace 是 403 NO_WORKSPACE,带 task_id 是 404)] → 与 distributions(资源型 404)和 aggregates(workspace 型 403)两个已合入先例分别一致;客户端按"是否指定资源"区分,契约已在 spec 固定。
- [分支逻辑内联于端点] → 全部构件均已存在,复用已有助手,保持两种模式的顺序和错误契约可见,不新增通用解析器。

## Migration Plan

单 API 文件增量 + 新测试文件。顺序:实现 → 新边界测试 → 回归报告 API/reconciliation/aggregate 与 distributions 边界测试 → 共享检查。回退即还原文件,无 schema 变更。

## Open Questions

(无——两种模式的门槛、顺序、错误形状均已在 delta spec 固定。)
