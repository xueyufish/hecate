## Context

P1 的模型路由只有简单 fallback 列表。P3 需要完整的模型路由引擎，支持 A/B 测试和灰度发布。Provider 策略模式（provider_shaping.py）可扩展为路由策略。

## Goals / Non-Goals

**Goals:**

1. 实现 ModelRouter — 按规则路由（成本/延迟/能力/随机）
2. 实现 ABTestManager — 流量分割 + 指标收集
3. 实现 GrayReleaseManager — 加权路由灰度发布

**Non-Goals:**

1. 不修改 LiteLLM 集成（在其上层封装）
2. 不实现模型精调（属于 P4）

## Decisions

### D1: 路由策略复用 ProviderStrategy 模式

**选择**：扩展 provider_shaping.py 的策略模式为路由策略

**理由**：
- 接口一致（shape context → select model）
- 可复用策略注册机制
- 渐进式扩展

### D2: A/B 测试基于流量分割

**选择**：按百分比分割流量到不同模型

**理由**：
- 简单直观
- 支持多模型对比
- 指标收集标准化

## Risks / Trade-offs

| 风险 | 缓解 |
|------|------|
| A/B 测试增加延迟 | 异步指标收集 |
| 灰度发布配置复杂 | 提供默认配置 |
| 路由决策不准确 | 提供 fallback |
