# Proposal: eval-publish-gate-versions (7.3a + 7.3b)

## Why

7.3 系列还剩两个 pending follow-up：**7.3a** publish 阻断开关与 **7.3b** 命名 dataset 版本管理。两者在 7.3 被刻意推迟——publish 是已发布的稳定契约，阻断语义需要独立评审；命名版本在快照机制跑稳之前会造出两套互相打架的真相机制。现在前置已就位：7.3c（known-bad 豁免）让 `pass_rate` 成为门禁敢读的诚实口径，快照机制（items + content hash + drift）经 7.3/7.3c 两轮迭代后语义稳定。单独看，7.3a 只能回答"最近一次 run 有没有回归"，7.3b 只能回答"每次 run 用了哪份数据"；合在一起才能回答发布工程真正的问题——**"这个待发布版本，是否被我们钉住的那份数据验证过"**。本 change 一次交付两者，并一并偿还 7.3c 推迟的 known-bad 标记 UI 欠账。

## What Changes

**7.3a — publish 评估门禁**

- `WorkflowModel` 新增 `evaluation_gate` JSON 列（nullable = 关闭）：模式 `warn` / `require`，信号开关 `min_pass_rate`、`block_on_regression`、`block_on_drift`、`require_run`、`require_dataset_version`。
- 门禁只读**确定性信号**：从 score 台账按 `source="deterministic"` 过滤，重算 per-item 确定性通过率（`deterministic_pass_rate`）与 per-metric 回归；LLM judge 与 human 分数永不参与阻断判定。
- publish API 契约变更（本 change 的核心评审点，opt-in 非破坏）：`mode=require` 且任一启用信号不满足时返回 **409 `EVALUATION_GATE_BLOCKED`**，错误信封 `details` 携带完整 gate 结果与 `evaluation_report`；请求体新增可选 `{"force": true}` 绕过（照常 200，report 标记 `gate.bypassed_by_force`，写审计）。`mode=off` / `warn` 下 publish 语义与今天逐字节一致。
- `evaluation_report` 增强：新增 `gate` 块（mode、逐信号判定、结论）与 `dataset_version` 块（run 绑定命名版本时：id / name / hash）。
- run summary 持久化生效的 `threshold`（门禁 per-item 判定与报告自描述的前提）。

**7.3b — 命名 dataset 版本**

- 新模型 `EvaluationDatasetVersionModel`：`dataset_id + name` 唯一、items 冻结（与 run 快照同一 canonical 序列化与 content hash 实现）、`content_hash`、`created_by`；创建后不可变，支持软删除。
- 版本 API：create（冻结当前 live items）/ list / get / delete / **checkout**（把版本 items 复制回 live dataset，响应带 diff 摘要）/ **diff**（vs 另一版本或 vs live）。
- run 绑定版本：评估触发请求可选 `dataset_version_id`；绑定 run 的快照与执行均取版本 items（成本护栏按版本 items 计数）；run 暴露 `dataset_version_id`。门禁信号 `require_dataset_version` 由此读取——"发布必须被钉住版本验证过"的最小闭环。

**UI 欠账偿还（web）**

- dataset 详情：版本列表 / 创建 / checkout / diff 视图（版本 vs 版本、版本 vs live）。
- item 列表：known-bad 标记入口（7.3c 显式推迟到本批次的界面工作）。
- workflow publish 流程：gate 配置入口 + 被阻断时的 report/gate 呈现 + force 二次确认。

## Capabilities

### New Capabilities

- `dataset-versioning`：命名 dataset 版本对象的完整生命周期——冻结（与 run 快照同一序列化与 hash）、不可变、软删除、checkout 恢复 live、版本间/对 live 的 diff。

### Modified Capabilities

- `workflow-version-publish`：新增 publish 评估门禁 requirement——`evaluation_gate` 配置校验、确定性-only 信号判定、409 阻断契约、force 绕过与审计。
- `workflow-evaluation`：publish-time report requirement 修改（新增 gate 块与 dataset_version 块；非阻断保证限定为 `mode=off/warn`）；新增版本绑定 run requirement（快照来源、执行 items、护栏计数）；repetition aggregation requirement 修改（summary 持久化 threshold）；CLI eval run requirement 修改（`--dataset-version-id` 选项）。

## Impact

- **Schema/迁移**：新表 `evaluation_dataset_versions`；`evaluation_runs` 加 `dataset_version_id`（nullable，索引）；`workflows` 加 `evaluation_gate`（JSON，nullable）。一个 Alembic 迁移，全部加性向后兼容，存量行零影响。
- **代码**：`ops/evaluation/publish_gate.py`（新，门禁判定）、`ops/evaluation/dataset_version_service.py`（新，版本生命周期）、`ops/evaluation/tasks/runner.py`（版本绑定快照 / items override / 护栏计数）、`ops/evaluation/engine.py`（threshold 落 summary、items override）、`ops/api/`（新版本路由 + trigger 请求参数）、`studio/workflows/service.py`（publish 接门禁 + force + report 增强）、`studio/api/workflows.py`（请求体）、`models/{evaluation,workflow}.py`、CLI（eval run 版本选项、publish 输出 gate 结果）。
- **契约**：publish API 的 409 与可选请求体是唯一行为性契约变更，且仅在用户显式开启门禁后生效——这正是 7.3 设计预留的"独立评审"点；`mode=off` 下响应与现状逐字节一致。
- **不受影响**：online 侧 `evaluation_task_scores` 台账与 7.4 人评 reconciliation；`POST /runs/compare`（读 summary）；runtime 聊天路径；7.2e 报表（summary 新键为加性）。
- **测试**：`tests/test_services/test_evaluation/`（publish_gate、dataset_version_service、runner 版本绑定）、`tests/test_api/`（版本端点、publish 409/force、trigger 绑定）、web `__tests__`。
