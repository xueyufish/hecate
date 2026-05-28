## Context

P2 的多 Agent 编排只支持 Graph 模板（层级委派、移交、流水线、广播），不支持动态任务分配和 P2P 协商。P3 需要更灵活的 Agent 间通信机制。

## Goals / Non-Goals

**Goals:**

1. 实现 AgentMessageBus — 事件驱动消息总线
2. 实现 DynamicTaskAllocator — LLM 驱动的动态任务分配
3. 实现 P2PNegotiator — Agent-to-Agent 自主协商协议

**Non-Goals:**

1. 不删除 Graph 编排（保留作为确定性模式）
2. 不实现强化学习优化（属于 P4）

## Decisions

### D1: 事件驱动消息总线作为可选通信层

**选择**：AgentMessageBus 作为可选层，与 Graph 编排并存

**理由**：
- Graph 编排适合确定性流程
- 消息总线适合动态协作
- 两种模式可混合使用

### D2: LLM 驱动的任务分配

**选择**：使用 LLM 分析任务描述，动态选择执行 Agent

**理由**：
- 静态 Graph 无法处理未知任务
- LLM 理解语义，能做智能路由
- 可配置 fallback 到静态路由

## Risks / Trade-offs

| 风险 | 缓解 |
|------|------|
| 动态分配增加延迟 | 缓存路由决策 |
| LLM 路由不准确 | 提供静态 fallback |
| 消息总线复杂度 | 保持接口简单 |
