# Design

## Context

`evaluation_reports.py` 的 overview/trends/breakdowns 三个入口把 `ctx.workspace_id` 直传 `EvaluationReportService`,无角色检查。`ensure_workspace_role(ctx, VIEWER)` 对三种情形的行为:system scope 直接放行(但 `ctx.workspace_id=None` 传给服务的 `workspace_id == None` 过滤会得到空报告——既有 system 行为,本次按 spec 改为 403);无 workspace/无角色抛 `WorkspaceRoleRequiredError`;角色不足抛同名异常。`NO_WORKSPACE` 403 码在 `channel/api/tasks.py` 已有同型约定。报告模块现状:distributions 已有资源解析边界(#298),本切片是同一文件内其余三个聚合入口的纯角色门槛。

## Goals / Non-Goals

**Goals:**

- 三入口服务调用前先显式检查 active workspace(含 system),缺失时返回 403 `NO_WORKSPACE`;再统一 `ensure_workspace_role(ctx, WorkspaceRole.VIEWER)`,无角色或角色不足时将 `WorkspaceRoleRequiredError` 映射为 403 `FORBIDDEN`。
- 服务只接收通过门槛的 `ctx.workspace_id`;零 UUID 是合法 workspace 值,不做 truthy 回退。

**Non-Goals:**

- 不给 system 新增全局聚合或 workspace 选择参数;不改 sessions/distributions;不改报告服务算法、签名与前端;不触碰两个在途 worktree 占用的文件。

## Decisions

1. **单个小助手 `_require_aggregate_viewer(ctx)` 统一三入口**,映射规则:先显式判 `ctx.workspace_id is None`(含 system)→ 403 `NO_WORKSPACE`;再 `ensure_workspace_role` 失败 → 403 `FORBIDDEN`(消息沿用助手原文,命名所需角色)。显式判空放在 `ensure_workspace_role` 之前,因为后者对 system scope 直接放行,会把 system-无 workspace 漏进服务的 `None` 过滤。备选"给 `ensure_workspace_role` 加严格模式"被否决:全局助手改动会波及所有既有调用方,超出单切片边界。

2. **403 `NO_WORKSPACE` 码沿用 channel 层约定**,响应包络与其余错误一致(`{"error": {"code": "NO_WORKSPACE", "message": ..., "details": None}}`),不新造形状。

3. **校验位置在服务调用之前、各端点函数体内第一行**,三处显式调用:与 distributions 端点的内联风格一致,保持改动局限于该路由文件,不改共享身份依赖。

## Risks / Trade-offs

- [system 调用从"空报告 200"变为"403 NO_WORKSPACE"] → 空报告本身就是 `None` 过滤的伪结果而非真实数据;spec 明确本切片不新增 system 全局聚合,真实跨 workspace 需求须走显式参数设计(留待后续)。
- [三入口各自内联调用,新增第四个聚合入口时需记得加门槛] → 单文件内三处调用相邻,review 可见;`sessions` 端点归后续切片处理时同样内联。
- [缺 workspace 与无角色的 403 拆分] → 两个 code 都是 403,仅 `code` 字段不同;`NO_WORKSPACE` 精确表达"无 active workspace"成因,与 channel 层约定对齐。

## Migration Plan

单 API 文件增量 + 新测试文件。顺序:实现门槛 → 新边界测试 → 回归报告 API/reconciliation/distributions 边界测试 → 共享检查。回退即还原文件,无 schema 变更。

## Open Questions

(无——门槛、`NO_WORKSPACE` 契约与 system 行为变更均已在 delta spec 固定。)
