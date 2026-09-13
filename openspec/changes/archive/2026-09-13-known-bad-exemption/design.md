# Design: known-bad-exemption (7.3c)

## Context

7.3 交付后，run 聚合（`EvaluationEngine._build_summary`）把所有 dataset item 一视同仁地计入 `pass_rate` / `consistency_rate` 的分子分母；item 上没有任何"这条数据本身是坏的"的表达方式。7.2b 合成、7.2d 回流、7.4 人评物化三个自动写入方让 dataset 成为持续演化的活对象，坏条目混入的概率随使用增长。业界调研（`docs/research/2026-09-agent-eval-practices-survey.md`）确认 item 级豁免无平台先例，最接近的原语是 Langfuse `ARCHIVED`（剔除不删除）、promptfoo `weight: 0`（仍采集信号、不参与判定）、DeerFlow 带过期时间的 CI waiver、openworker 的逐次调用审批溯源。

代码锚点：item 模型 `src/hecate/models/evaluation.py`（`EvaluationItemModel`，已有 JSON `tags` 列先例）；快照 `src/hecate/ops/evaluation/tasks/runner.py` `_snapshot_dataset`（显式字段清单 + canonical JSON + sha256）；聚合 `src/hecate/ops/evaluation/engine.py` `_build_summary`（`item_scores: dict[item_id, list[Score]]` 上算通过数）与 `_compute_regressions`（metric 平均 + 5% 默认回归阈值）；item CRUD `src/hecate/ops/evaluation/dataset_service.py`。

## Goals / Non-Goals

**Goals:**

- item 级 known-bad 豁免：带 reason 与审计 provenance（谁/何时标记），可标记、可解除、可过滤、可导入导出。
- 聚合排除语义：豁免 item 仍执行、分数仍落库，但不进 `pass_rate` / `consistency_rate` / `metric_averages` / 回归判定的分子分母；summary 暴露 `exempted_items`。
- 豁免项通过的"自愈"报警信号（`known_bad_passed_item_ids`），只报警不自动解除。
- 快照语义：豁免状态冻结进 run 快照（历史 run 不被追溯改写）；豁免字段不参与 content hash（标记不触发 `dataset_drift`）。

**Non-Goals:**

- 不实现到期强制逻辑（`known_bad_expires_at` 仅预留列；DeerFlow 式到期重审为后续可加项）。
- 不做"自动解除豁免"（自愈信号只报警）。
- 不动 online 侧：`evaluation_task_scores` 台账、7.4 人评 reconciliation、breakdowns raw 口径均不受影响（豁免是 item 级离线 run 概念）。
- 不做 run-report / dataset 管理 UI 的标记入口按钮（API-first；UI 归入 7.3a/b 批次的界面工作一并考虑）。
- 不引入与 7.4 标注队列的联动（known-bad 是 item 元数据，不是一条 human score；两本账混写会污染 calibration 的 agreement 口径）。

## Decisions

### D1. 专列存储，不用 tags 约定，不落 score 台账
三个候选：`tags` JSON 里约定一个保留 tag；`EvaluationTaskScoreModel` 里加一条 `source="human"` 的 known-bad 评分行；item 专列。选专列：豁免改变的是**分母语义**，是强类型、需可查询、需审计 provenance 的结构性事实，自由 JSON tag 承载不了；score 台账是"对一次执行的评分"，而豁免是"对数据本身的断言"，混入会让 7.4 calibration 把豁免当评分算 agreement。与 7.3 D3（subject 与 score target 分字段）同一思路。
**评估**：tag 约定——零迁移但查询/索引/类型全靠约定，误用面大；score 行——复用 7.4 通路但语义污染不可逆。

### D2. 豁免 = 仍执行仍记录、排除出分子分母（promptfoo `weight: 0` / pytest xfail 语义），不是 skip
跳过执行会让豁免 item 失去观测信号——"豁免项突然通过了"这个自愈信号恰恰需要它照常执行才存在；skip 还会改变 run 的执行数与成本画像。排除聚合但保留执行，是 xfail(non-strict) 与 skip 的本质区别，也是 promptfoo `weight: 0` 验证过的形态。
**评估**：skip 执行——省一点执行成本，代价是失明。

### D3. 豁免字段不参与 content hash；canonical 序列化稀疏携带
快照 hash 目前覆盖 item 的六个内容字段。豁免是**元数据**而非评估内容：如果把 `known_bad` 纳入 hash，每次标记都会让所有下游消费方看到一次假 `dataset_drift`（7.2e 视图、compare、CLI exit 2 全部被噪声触发）。决定：hash 只算 `query/expected_answer/context/tags/metadata`；豁免标记字段仅在已标记时附加到快照 item dict 上（稀疏序列化），供聚合读取。这同时天然化解了迁移部署日的 hash 漂移问题——存量 dataset 序列化结果逐字节不变。
**评估**：hash 含豁免——语义"自洽"但每个消费方都要吞标记噪声；稀疏 + 排除 hash——hash 语义变成"评估内容指纹"，更准确。

### D4. 聚合读快照时的豁免状态，历史 run 不追溯
豁免状态跟随 item 冻结进 `dataset_snapshot`（与 7.3 D2"启动时锁定"同构）。run 完成后标记/解除只影响之后的 run。compare 两端各自按自己的快照口径排除——豁免变更横跨的两次 run 平均值不可直接比，这与任何 dataset 变更一致，属既有语义，不新增机制。
**评估**：聚合时读 live 状态——实现省事，但"已出的报告会变脸"违背 run 记录的不可变契约。

### D5. 自愈信号只报警，不自动解除
`known_bad_passed_item_ids` 是提示，不是动作。dataset 已有三个自动写入方（7.2b/7.2d/7.4），自动解除豁免会成为第四个隐式变更源，且"通过"可能是 expected 被绕过而非被满足（LLM 非确定性）。解除必须是人审后的显式动作。
**评估**：自动解除——省一次点击，引入不可解释的数据突变。

### D6. Provenance 服务端填充；reason 必填；`expires_at` 只留列
`known_bad_marked_by` 取当前认证用户，客户端不可直接写（openworker 审批溯源模式）；无 reason 的标记拒绝（豁免必须可解释，否则 pass_rate 的口径变化无法归因）。`known_bad_expires_at` 可空列随迁移一并落库，v1 无任何读取方——DeerFlow 的到期重审模式证明该机制有价值，但引入 cron/扫描逻辑，值得独立成后续小特性。
**评估**：客户端自填 provenance——实现省一次查询，审计链不可信。

### D7. 排除逻辑收敛在聚合单点
`_build_summary` 与 `_compute_regressions` 是唯二改动点；compare、reports、CLI 全部读 summary/metric_averages，自动继承排除后口径，不新增平行计算。7.2e breakdowns 保持 raw（与 7.4a reconciliation 的"breakdowns 保留 raw"先例一致）。
**评估**：各消费方自行过滤——口径漂移的温床。

## Risks / Trade-offs

- **R1: 豁免被滥用刷 pass_rate**（把难看的 item 全豁免，指标失真）→ reason 必填 + provenance 落库 + summary 恒久暴露 `exempted_items`（报表可见，藏不住）；到期重审（后续特性）是第二道闸。
- **R2: import/export 遇到无标记字段的旧导出文件** → import 将缺失字段按"未标记"处理，不报错；导出始终带全字段。
- **R3: hash 语义变化让"两次 run hash 相同但豁免不同"** → 这是设计意图（同内容同指纹）；`exempted_items` 计数已把豁免差异显式暴露在 summary 上。
- **R4: 并发标记与进行中 run** → 快照时点语义使其天然安全：run 读快照，标记写 live，互不阻塞。
- **R5: 下游误读 `known_bad_passed_item_ids` 为自动动作** → 字段命名与文档明确其为提示信号；UI（后续批次）展示为徽标而非按钮。

## Migration Plan

- 单个 Alembic 迁移：`evaluation_items` 加五列（`known_bad` bool 默认 false 非 null；其余四个 nullable），随 PR 走；存量行零影响、无需 backfill。
- 回滚：`downgrade()` 删列；无数据损失（豁免标记本就是可重建的元数据）。
- 部署无顺序要求：加列向后兼容，旧代码忽略新列。

## Open Questions

（无。到期重审的强制逻辑已在 Non-Goals 显式推迟，不阻塞本 change 的任何决策。）
