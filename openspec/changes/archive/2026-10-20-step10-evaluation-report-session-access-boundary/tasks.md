# Tasks

## 1. 边界实现

- [x] 1.1 改造 `src/hecate/ops/api/evaluation_reports.py` 的 `sessions_report` 为双模式:`task_id is None` → `_require_aggregate_viewer(ctx)` 后服务用 `ctx.workspace_id`;带 `task_id` → 显式缺 workspace 查询前 `_report_scope_not_found()`(404)→ `_get_visible_task_or_404`(同体无回显 404)→ `ensure_workspace_role(ctx, VIEWER)` 失败 `_forbidden` → 服务用 `task.workspace_id`。零新增助手,全部组合既有函数。验证:新边界测试通过。
- [x] 1.2 保持聚合、reconciliation、排序、分页、时间窗口与响应形状不变;distributions 与三个聚合入口的既有边界不动。验证:既有报告测试与 ruff/mypy 通过。

## 2. 边界测试

- [x] 2.1 新增 `tests/test_api/test_evaluation_reports_session_boundary.py`:两种模式 × VIEWER/EDITOR/ADMIN 真实数据成功;零 UUID 与非零 workspace 双向隔离;同 workspace 的 Task 过滤;workspace 模式无角色/缺 workspace/system 查询前拒绝;Task 模式可见无角色 403、缺失/已删除/跨 workspace 恒定无标识 404,涵盖跨 workspace 无角色的归属优先;普通身份缺 workspace 带 task_id 查询前 404;system 使用非零目标 workspace、拒绝缺失/已删除 Task(包括残留评分);合法空结果;参数校验保持;全部拒绝不触达聚合服务,成功/拒绝均检查 Task/TaskScore 全表快照。验证:该文件全部通过。
- [x] 2.2 回归既有 `test_evaluation_reports_api.py`(含 sessions 既有用例)、`test_reports_reconciliation.py`、`test_evaluation_reports_aggregate_boundary.py`、`test_evaluation_reports_distribution_boundary.py`。验证:相关测试全部通过。

## 3. 门禁与收尾

- [x] 3.1 运行共享门禁并留存实际结果:`python scripts/dev.py check all`(ruff/mypy)、受影响范围 pytest(新增文件及 2.2 列出的四个测试文件)。验证:结果写入 verification.md。
- [x] 3.2 运行 `openspec validate "step10-evaluation-report-session-access-boundary" --strict`;功能清单增量核对(预期零新增条目)并在 change 目录写 `verification.md` 记录实际结果与未测范围。验证:全部通过并完成记录。
