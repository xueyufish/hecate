# Tasks

## 1. 边界实现

- [x] 1.1 改造 `src/hecate/ops/api/evaluation_reports.py` 的 `distributions_report`:参数互斥校验保留原 400 消息 → 缺 workspace 非 system 在 SELECT 前拒绝 → Run scope 经 `_get_visible_run_or_404`、Task scope 经新增 `_get_visible_task_or_404`(归属优先,Run helper 的 404 在本入口转换为与 Task 相同的完整报告错误体)→ `ensure_workspace_role(ctx, WorkspaceRole.VIEWER)`(失败 `_forbidden`)→ 服务调用传解析资源的真实 `workspace_id`。验证:新边界测试通过。
- [x] 1.2 新增 `_get_visible_task_or_404`(`EvaluationTaskModel` 按 id + `~deleted` + workspace 归属,常量 404)与 `_report_scope_not_found()`(无回显常量 404);`_run` 的 NotFound 映射改为该常量,消除 `Report scope {id}` 回显。保持 400/422 与成功响应形状不变。验证:ruff/mypy 通过,相关测试回归。

## 2. 边界测试

- [x] 2.1 新增 `tests/test_api/test_evaluation_reports_distribution_boundary.py`(镜像既有报告 API 测试夹具):Run/Task 两 scope 的 viewer 成功(形状不变)、无角色同 workspace 403、缺失/已删除/跨 workspace 同体 404 且不回显 ID、system 用真实 workspace 取到真实直方图(非空)、无 workspace 非 system 404、双方提供/均不提供 400 消息原文、非法 UUID 422、拒绝路径零副作用(全表快照)。验证:该文件全部通过。
- [x] 2.2 回归既有 `test_evaluation_reports_api.py`、`test_reports_reconciliation.py` 与 Run 读边界测试,确认聚合、reconciliation 与既有边界无回归。验证:相关测试全部通过。

## 3. 门禁与收尾

- [x] 3.1 运行共享门禁并留存实际结果:`bash scripts/dev.sh check all`(ruff/mypy)、受影响四个 API/服务测试文件的 pytest(详见 verification.md)。验证:结果写入 verification.md。
- [x] 3.2 运行 `openspec validate "step10-evaluation-report-distribution-access-boundary" --strict`;功能清单增量核对(读边界属既有 dashboard 能力收敛,预期零新增条目)并在 change 目录写 `verification.md` 记录实际结果与未测范围。验证:全部通过并完成记录。

## 4. 复审修正

- [x] 4.1 补失败回归确认 Run/Task 404 body 不一致、缺 workspace 仍执行 SELECT,修正入口映射与提前拒绝,不修改被复用的 Run helper。
- [x] 4.2 扩展 viewer/editor/admin 与无角色的矩阵、跨 scope 同体 404、非零 workspace 的 system 真实取数与异 workspace 分数排除、system 缺失/删除资源、服务再次查询失效、拒绝路径数据库状态不变。修正 role=None fixture 不应插入默认 viewer 成员行。
- [x] 4.3 运行相关回归与共享检查,以实际复审结果替换 verification.md 中初版记录,并记录未测范围。
