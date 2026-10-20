# Design

## Context

分数分布服务已按 workspace 和软删除状态过滤 Run/EvaluationTask 及其分数,但 router 未检查 viewer 角色,NotFound 映射还会回显 ID。system 的 workspace 为 None,直传服务会使正常资源被判定不存在。已有 `_get_visible_run_or_404` 可只读复用;其错误文本为 Run 专用,在报告入口须规范化为共同报告错误体。

## Goals / Non-Goals

- 只收敛 distributions 的 Run/EvaluationTask 两种 scope:参数校验 → 缺 workspace 拒绝 → 资源可见性 → viewer 角色 → 单 workspace 聚合。
- 不可见资源与服务再次查询失效均返回同一无标识报告 404。
- system 使用指定资源的真实 workspace,不引入全局聚合。
- 不修改其他报告入口、报告服务算法/签名、Run helper、前端、Runtime、共享 skill 或其他在途文件。

## Decisions

1. **保留参数契约。** UUID 解析仍由 FastAPI 返回 422;双方提供/均未提供 scope 的 400 消息保持原文,位于授权与资源解析之前。
2. **缺 workspace 提前拒绝。** 参数有效时,`not ctx.is_system_scope and ctx.workspace_id is None` 在任何资源 SELECT 前返回 `_report_scope_not_found()`。None 不解释为 system 或全局范围。
3. **资源归属先于角色。** Run 复用 `_get_visible_run_or_404`;只在此报告入口将其 404 转为报告常量,保留其他 HTTP 异常。EvaluationTask 在本文件使用小型解析器检查 id、`~deleted` 与 workspace。缺失/删除/跨 workspace 的 Run 与 Task 使用相同完整 404 body。可见资源的无角色身份才返回 403;viewer/editor/admin 均可读。
4. **按资源 workspace 查询分数。** 资源解析通过后调用 `ensure_workspace_role(ctx, WorkspaceRole.VIEWER)`,再以资源的 workspace 调用原服务。system 只豁免调用方 workspace/角色约束,仍不能读取缺失或已删除资源。
5. **保留服务层再次校验和错误映射。** `_run` 将 `EvaluationReportNotFoundError` 规范化为相同报告 404,覆盖预检查后资源失效的路径;原聚合、分箱、错误分数排除与 reconciliation 不变。
6. **测试使用真实 API 与既有 SQLite 夹具。** 非零 workspace 覆盖普通成员与 system 成功;向相同 scope 注入异 workspace 分数,确认不会混入结果。拒绝路径比较持久列快照,并断言无 workspace 不执行 SELECT、无角色不进入聚合。用服务异常验证再次查询失效映射。

## Risks / Trade-offs

- 角色收紧只拒绝原先越权成功的无角色请求,viewer 保持合法读取能力。
- 报告 404 文本统一为 `Report scope not found`,code 仍为 `NOT_FOUND`;Run 主 API 的既有错误体保持原样。
- 复用 Run helper 需要局部转换其 HTTP 404;不为两个模型引入新的公共授权抽象。
- 本切片没有关闭所有评测报告入口的权限缺口,其他入口仍需独立审查。

## Migration Plan

无 schema 迁移。修改单一 router、新增独立测试与本 change artifacts。回归报告 API/reconciliation/Run 读边界,运行共享检查。回退还原本 router 即可。

## Open Questions

无。
