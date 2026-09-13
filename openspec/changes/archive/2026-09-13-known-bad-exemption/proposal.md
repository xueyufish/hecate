# Proposal: known-bad-exemption (7.3c)

## Why

Workflow Evaluation（7.3）的 `pass_rate` 把所有 dataset item 一视同仁：一条 expected 已过时、标注错误或依赖外部环境已失效的 item 会永久性地拉低每次 run 的通过率，噪声大到掩盖真实回归，也让 7.3a（publish 阻断门）未来读到的信号不可信。业界横向调研（`docs/research/2026-09-agent-eval-practices-survey.md`）确认：没有任何平台提供 item 级 known-bad/xfail 豁免原语——最接近的仅有 Langfuse 的 ARCHIVED（剔除不删除）与 promptfoo 的 `weight: 0`（只收集信号不参与判定）。这是 positioning 级差异化，且是 7.3 系列三个 follow-up（7.3a/b/c）中最小、应最先落地的一个。

## What Changes

- `EvaluationItemModel` 新增豁免标记字段：`known_bad`（bool，默认 False）、`known_bad_reason`（必填当 known_bad=true）、`known_bad_marked_by` / `known_bad_marked_at`（服务端从当前用户与时间填充，审计 provenance）、`known_bad_expires_at`（可空，预留 DeerFlow 式到期重审，v1 不实现到期逻辑）。
- Dataset item 更新 API 支持设置/解除豁免标记；import/export JSON 完整 round-trip 豁免字段；`list_items` 支持按豁免状态过滤。
- Run 聚合语义改变：known-bad item **仍然执行、分数仍然落库**，但不进入 `pass_rate` / `consistency_rate` 的分子与分母，也不进入 `metric_averages` 与回归判定（`is_regression`）；`summary` 暴露 `exempted_items` 计数。
- "豁免项突然通过"成为显式信号：`summary.known_bad_passed_item_ids` 列出全部通过的豁免 item（pytest strict-xfail 的 "dataset healed" 语义）。**只报警，不自动解除豁免**。
- Run 快照行为：豁免状态随 item 冻结进 `dataset_snapshot`（聚合读取快照时的标记，标记后改不影响历史 run）；快照 canonical 序列化对豁免字段稀疏（未标记的 item 序列化不变）；**豁免字段不参与 content hash**——标记/解除豁免不触发 `dataset_drift`（豁免是元数据，不是评估内容变更）。
- CLI `hecate workflow eval run` 三态退出码语义自动继承（基于排除后的 pass_rate），无需改动。

## Capabilities

### New Capabilities

（无——全部为既有 capability 的行为变更。）

### Modified Capabilities

- `evaluation-dataset`：item 契约新增 known-bad 豁免标记字段（含 provenance 与预留到期列）、标记/解除 API 行为、import/export round-trip、按豁免状态过滤。
- `workflow-evaluation`：run 聚合的豁免排除语义（pass_rate / consistency_rate / metric_averages / is_regression 的分子分母排除）、summary 新增 `exempted_items` 与 `known_bad_passed_item_ids`、快照冻结豁免状态 + 豁免字段不参与 hash 的快照语义。

## Impact

- **Schema/迁移**：`EvaluationItemModel` 加列（全部可空或带默认值，旧行零影响）；Alembic 迁移一个。
- **代码**：`ops/evaluation/dataset_service.py`（update/import/export/list 过滤）、`ops/evaluation/tasks/runner.py`（`_snapshot_dataset` 稀疏序列化 + hash 排除）、`ops/evaluation/engine.py`（`_build_summary` / `_compute_regressions` 排除逻辑）、`models/evaluation.py`、`ops/api/evaluation.py`（items 端点 schema）。
- **不受影响**：online 侧 `evaluation_task_scores` 台账与 7.4a 人评 reconciliation（豁免是 item 级离线概念）；`POST /runs/compare`（读 summary，自动继承排除后口径）；runtime 聊天路径。
- **消费方**：7.2e 报表（overview/trends 读 runs.summary，自动继承排除后口径；breakdowns 保持 raw）；未来 7.3a publish 门将读到诚实口径的 pass_rate。
- **测试**：`tests/test_services/test_evaluation/`（engine、dataset_service、offline_task_runner）、`tests/test_api/test_evaluation_api.py` 新增豁免场景。
