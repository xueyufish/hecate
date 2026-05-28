## Context

P3 的 Harness 优化闭环只能生成约束规则，不能自动改进 Agent 本身。P4 需要自我进化能力——从失败中学习，自动改进策略。参考调研结果中的 FATE 和 TRACE 模式。

## Goals / Non-Goals

**Goals:**

1. 实现 TrajectoryAnalyzer — 分析成功/失败轨迹
2. 实现 PolicyEvolver — 基于分析自动调整策略
3. 实现 SyntheticEnvironmentGenerator — 生成合成训练环境
4. 实现 OnPolicyTrainer — 基于实际执行数据的在线训练

**Non-Goals:**

1. 不实现完整的 RL 训练（太复杂，P4 研究方向）
2. 不实现模型精调（属于 P4 模型增强）

## Decisions

### D1: 轨迹分析使用 LLM

**选择**：使用 LLM 分析成功/失败轨迹，提取改进点

**理由**：
- LLM 理解语义，能做准确分析
- 可扩展分析维度
- 与 Harness 优化闭环复用

### D2: 策略调整基于规则而非 RL

**选择**：基于规则的策略调整，而非强化学习

**理由**：
- RL 训练复杂度高
- 规则调整可解释
- 渐进式：规则→RL

## Risks / Trade-offs

| 风险 | 缓解 |
|------|------|
| 自动调整引入新问题 | 人工审核 + 回滚机制 |
| 训练数据不足 | 合成环境补充 |
| 策略调整不准确 | A/B 测试验证 |
