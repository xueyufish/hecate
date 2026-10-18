# Proposal

## Why

Agent 发布门禁原先只统计有有效确定性分数的样本。evaluator 异常或空输出可能让缺证据样本退出分母，出现“十个样本一个评测失败、其余全过，按九个样本得到满通过率”的误放行。版本绑定已在前一切片完成，本 change 补齐演进方案的“缺少必须样本时拒绝”要求。

## What Changes

1. AgentVersion 绑定运行在实际样本、tags 和快照豁免确定后、答题开始前冻结 evaluation_scope：schema_version、item_ids、required_metrics、exempted_item_ids、dataset_snapshot_hash。只要求当前内置确定性 evaluator 的指标；LLM judge 不成为必需门禁证据，自定义 evaluator 不凭同名自动获得支持。
2. 发布逐项校验“非豁免样本 × 必需指标”：只接受本 workspace、当前 Run、未删除、source=deterministic 且数值在有效范围内的分数。重复分数不能补齐其他指标。缺证据与正常低分分别报告。
3. 空、畸形、未知版本或缺失范围，零必需样本/指标均不得空转通过；已有范围不允许被重入时的新配置覆盖。历史不补造，重新评测产生证据。
4. Agent 的质量与回归仍使用既有阈值和公式，但输入限定为同一冻结范围；豁免或越界分数不能影响质量，启用回归时基线也必须完整且必需指标可比较。共享 publish_gate.py 和 Workflow 既有行为不变。
5. require 拒绝不完整/无法核验的证据；warn 返回失败报告并放行；force 沿用先成功审计、再发布及 bypass 非通过语义。增量迁移无回填，存在范围证据时禁止删列降级。

## Capabilities

### New Capabilities

无。

### Modified Capabilities

- evaluation-tasks：绑定运行冻结评测范围及逐项完整性核验。
- agent-versioning：按冻结范围核验候选与回归基线，缺证据和低分分别报告。

## Impact

新增 evaluation_scope JSON 列与增量迁移；改动 engine、Agent versioning，范围生成、校验及分数筛选集中到 ops/evaluation/evidence.py。读取 schema 增加范围信息，不额外暴露 agent_snapshot 原始执行配置。

使用 full spec-driven：本切片改变持久化证据和发布门禁语义。不涉及 Runtime、durable、外部 EvaluationBackend、完整 evaluator 版本体系、Workflow repetitions、在线任务或前端，与在途 step7 无实现重叠。
