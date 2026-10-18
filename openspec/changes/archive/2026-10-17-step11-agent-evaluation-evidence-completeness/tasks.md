# Tasks

## 1. 模型与迁移

- [x] 1.1 `src/hecate/models/evaluation.py`：`EvaluationRunModel` 新增 `evaluation_scope: Mapped[dict | None]`（JSON；docstring 说明版本与范围字段与 NULL=未冻结），`EvaluationRunReadSchema` 同步字段。验证：mypy 通过；schema 往返含新字段。
- [x] 1.2 新增 expand-only alembic 迁移（加 JSON 列，锚 `017_agent_version_eval_binding`，无范围证据时可删列，有范围证据时拒绝降级）。验证：`alembic heads` 单一；如本地 PG 可达则实跑 `tests/test_migrations/test_upgrade_drift.py`。

## 2. 范围冻结（引擎）

- [x] 2.1 `EvaluationEngine.run`：在 items 列表确定后、任何答题开始前冻结 `evaluation_scope`（`item_ids` / `required_metrics`（仅当前内置确定性指标） / `exempted_item_ids` / `dataset_snapshot_hash`），豁免集按既有两源语义（快照 `_snapshot_exempted_ids` / live `it.known_bad`）；未声明版本不冻结。验证：单测覆盖绑定冻结（含 known-bad 与 tags 结清）、live 不冻结两态。
- [x] 2.2 冻结时机与 `_bind_agent_version` 顺序核对：绑定失败 → 运行 FAILED 且无范围；绑定成功 → 范围落列后才进入答题。验证：既有绑定测试加断言仍绿。

## 3. 门禁覆盖核验

- [x] 3.1 `AgentVersioningService._evaluate_gate` 追加 `evidence_coverage` 信号：无范围（历史绑定运行）→ `missing_evaluation_scope`；有范围 → 按可信分数查询和冻结集合核验，detail 含 `missing_count` 与截断的 `missing_samples` 定位；`warn` 计算报告不阻断；force 复用 #270 审计语义。验证：单测覆盖完整/缺失/无范围/warn/force。
- [x] 3.2 `publish_gate.py` 零修改核对（共享模块不动，Agent 调用方按冻结范围补前置条件与筛选质量输入）。验证：`git diff` 不含 `publish_gate.py`；既有 publish_gate 测试零放宽全绿。

## 4. 回归与验证

- [x] 4.1 门禁行为回归（主断言）：全过放行；1 样本缺 1 指标拒绝并定位；-1 错误分与生成失败计缺证据；低分样本质量失败但覆盖通过（两者分列）；重复分数不补另一指标缺口；known-bad 豁免不参与；任务/数据集修改不影响已冻结范围；历史无范围 require 拒绝、warn 报告放行；force 审计 + bypass 标记。验证：`tests/test_api/` 绑定测试文件内新增用例全绿。
- [x] 4.2 引擎冻结回归：绑定运行范围落列时机（含 known-bad 豁免来源、tags 结清）；live 未声明版本运行零行为变化。验证：既有绑定测试目录新增用例全绿。
- [x] 4.3 既有回归显式收集：绑定套件、agent 版本化/门禁 API、评测任务 API、评测服务全套、prompt 优化/适配器兼容、分层。验证：全部通过，既有断言零放宽（门禁语义变更涉及的既有用例如需调整逐一说明理由）。
- [x] 4.4 共享检查 `bash scripts/dev.sh check all`。验证：0 错误。
- [x] 4.5 汇总实际结果到 `verification.md`（命令输出要点、收集数、迁移实跑情况、边界与保留限制）。验证：实际结果已写入 verification.md；提交/推送与 PR/CI 留在后续用户流程。


## 5. 复核修正

- [x] 5.1 确定性指标范围与可信分数来源一致；范围版本/形状校验及空范围拒绝；冻结范围冲突拒绝改写。
- [x] 5.2 质量与基线计算使用同一冻结范围，排除豁免、越界、删除和跨租户分数；移除额外快照 API 暴露。
- [x] 5.3 迁移回退保留已有范围证据。
- [x] 5.4 增补漏洞回归、执行共享检查及 PostgreSQL 迁移验证并同步工件与实际结果。
