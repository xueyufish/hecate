## Context

P1-P2 没有 Harness 优化闭环——失败案例不会自动归因、不会生成约束规则、不会注入下次会话。P3 需要让系统具备自我改进能力。参考调研结果中的 AgentRx 和 REFLEX 模式。

## Goals / Non-Goals

**Goals:**

1. 实现 FailureAnalyzer — LLM 驱动的失败归因分析
2. 实现 ConstraintGenerator — 从失败自动生成约束规则
3. 实现 ConstraintInjector — 约束规则注入下次会话

**Non-Goals:**

1. 不实现强化学习优化（属于 P4）
2. 不实现合成环境生成（属于 P4）

## Decisions

### D1: 失败归因使用 LLM 分类

**选择**：使用 LLM 分析失败轨迹，分类为 10 种失败类型

**理由**：
- 参考 AgentRx 的 10 种失败分类
- LLM 理解语义，能做准确归因
- 可扩展分类体系

### D2: 约束规则注入 system prompt

**选择**：将约束规则追加到 system prompt

**理由**：
- 简单直接
- 不修改 LLM 服务
- 可配置启用/禁用

## Risks / Trade-offs

| 风险 | 缓解 |
|------|------|
| 归因不准确 | 人工审核 + 反馈循环 |
| 约束规则冲突 | 优先级机制 |
| system prompt 膨胀 | 约束规则数量限制 |
