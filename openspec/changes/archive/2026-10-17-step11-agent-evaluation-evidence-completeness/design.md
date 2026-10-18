# Design

## Context

前一 change 已把 Agent、版本、配置哈希及快照绑定到 Run。共享发布评分仅消费现有有效确定性分数，无法区分“全量评测通过”与“剩余可见证据通过”。必须先冻结预期范围，再判定实际覆盖；不能在发布时从可变任务或数据集推导预期范围。

## Goals / Non-Goals

目标：保存不可改写范围、校验完整性、区分缺分与低分、保持 require/warn/force 与租户隔离、保留历史证据。

非目标：外部评估服务和完整版本体系、Workflow 重复执行、线上抽样、前端、Runtime/Controller、完整发布制品。LLM judge 结果仍可用于报告，但其异常不作为确定性发布门禁的新增失败条件。

## Decisions

### D1: Run 自有的版本化范围

evaluation_scope 为 nullable JSON，包含 schema_version=1、实际 item_ids、当前支持的 required_metrics、exempted_item_ids 与可为空的 dataset_snapshot_hash。item_ids 包含实际执行项，豁免是它的子集。tags 在 live 列表筛选时结清，items_override 按既有冻结样本语义使用；快照中的 known-bad 标记优先于活行。未被选中的豁免 id 不进入范围。

冻结发生在答题前，started_at 计时标记之后。已有范围若与本次输入不同，运行失败并保留原范围，不静默改写。未绑定运行不冻结。无范围的历史 Run 不回填。版本、UUID、列表形状/重复项、必需指标、豁免子集、快照哈希形状及与 Run 快照的一致性均在消费时校验，未知证据报告失败而非 HTTP 500。

### D2: 只冻结当前内置确定性指标

当前支持 ContainsEvaluator、ExactMatchEvaluator、IsJsonEvaluator、RegexMatchEvaluator、PiiLeakageEvaluator 及其可验证的测试子类。范围由实际 evaluator 实例生成；不按分数事后猜测来源，也不接受自定义对象仅凭同名宣称内置支持。这些实现的 name 与单一 metric_name 一致。

source=human/llm_judge 不能填确定性缺口。零必需指标、零样本或全部豁免不得产生通过的覆盖结论。内置实现未来增加/改变输出时须显式更新该支持面；本次不建外部 evaluator 注册协议。

### D3: 可信覆盖与质量使用同一输入集合

evidence.py 按 Run id 取分数，在 workspace、source、删除状态、数值范围和有限性检查后，用冻结样本/指标集合筛选。expected_count 为必需样本数 × 指标数，实际覆盖按 pair 去重；差集定位最多展示二十项，总缺失计数不截断。避免 SQL 大 IN 参数及完整差集物化。

缺分、负分及无效来源进入 evidence_coverage。普通低分有效、覆盖通过，但可能使 min_pass_rate 失败。Agent 调用方沿用原有逐样本阈值及回归公式，只把可用分数限制在冻结的非豁免范围内；不让 known-bad 分数或额外样本/指标稀释质量。覆盖独立失败，部分有效分数的高通过率不能让整体门禁通过。

启用回归时，当前 published_version 的匹配基线也执行覆盖检查；缺基线、基线不完整或必需指标集不一致均明确失败，不能跳过缺失指标后报告无回归。更完整的数据集/评估器版本可比较性体系仍留在后续能力。

### D4: 既有发布模式与 API 边界

共享 publish_gate.py 保持不变，Agent 调用方追加覆盖前置条件并以范围内分数计算结果。未配置门禁直接发布；warn 如实报告失败但不阻断；force 必须先成功审计，保留 passed=false 和 bypass 标记。读取 schema 仅增加 evaluation_scope，不新增原始 agent_snapshot 暴露。

### D5: 迁移与回退

018_evaluation_scope 接到 017_agent_version_eval_binding，新增 nullable sa.JSON，无回填。无范围记录时允许 downgrade/re-upgrade；存在任意已保存范围时拒绝删除该列。应用代码回退保留增量列；不得把证据列删除描述为无数据风险。

## Risks / Trade-offs

- 历史无范围、未知格式和无受支持指标的运行在 require 下需要重新评测；不按当前配置补造。
- 当前只覆盖内置确定性指标，不声明完整 evaluator 版本或自定义 evaluator 认证。
- evaluator 失败仍保持既有逐项隔离、Run 可完成，发布完整性单独失败；不强制中断其他样本。
- 本地模型替身和迁移验证不替代真实 provider 质量评测或合入 CI。
