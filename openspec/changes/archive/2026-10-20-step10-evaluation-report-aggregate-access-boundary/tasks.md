# Tasks

## 1. 门槛实现

- [x] 1.1 在 `src/hecate/ops/api/evaluation_reports.py` 新增 `_require_aggregate_viewer(ctx)`:显式判 `ctx.workspace_id is None`(含 system scope)→ 403 `NO_WORKSPACE`;否则 `ensure_workspace_role(ctx, WorkspaceRole.VIEWER)` 失败 → 403 `FORBIDDEN`。overview/trends/breakdowns 三入口在服务调用前接入。验证:新边界测试通过。
- [x] 1.2 保持成功路径与参数语义不变:响应形状、时间窗口、分桶、指标过滤、reconciliation、原始分 breakdowns、空报告语义均不改;distributions 与 sessions 端点不动。验证:既有报告测试与 ruff/mypy 通过。

## 2. 边界测试

- [x] 2.1 新增 `tests/test_api/test_evaluation_reports_aggregate_boundary.py`(镜像既有报告测试夹具):三入口 × viewer/editor/admin 成功(形状与数据不变)、无角色 403 `FORBIDDEN`、无 workspace 非 system 403 `NO_WORKSPACE`、system(无 workspace)403 `NO_WORKSPACE`、空 workspace 正常空报告、双 workspace 数据隔离、成功与拒绝路径零副作用(四张报告表全表快照)、拒绝发生在数据库查询与聚合服务调用之前。验证:该文件全部通过。
- [x] 2.2 回归既有 `test_evaluation_reports_api.py`、`test_reports_reconciliation.py`、`test_evaluation_reports_distribution_boundary.py`,确认报告语义与既有边界无回归。验证:相关测试全部通过。

## 3. 门禁与收尾

- [x] 3.1 运行共享门禁并留存实际结果:`python scripts/dev.py check all`(ruff/mypy)、受影响范围 pytest(新边界测试及 2.2 列出的三个测试文件)。验证:结果写入 verification.md。
- [x] 3.2 运行 `openspec validate "step10-evaluation-report-aggregate-access-boundary" --strict`;功能清单增量核对(读边界属既有 dashboard 能力收敛,预期零新增条目)并在 change 目录写 `verification.md` 记录实际结果与未测范围。验证:全部通过并完成记录。
