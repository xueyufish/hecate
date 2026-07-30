## Context — 背景

Hecate 有两条执行路径：
1. **对话路径**: API → ConversationService → LLM（直接）
2. **图路径**: PregelRuntime → AgentWorker → EnginePort → ConversationService → LLM

两条路径都需要上下文管理（消息选择、压缩、token 估算）。当前：
- ConversationService 有自己的上下文逻辑（ContextAssembler、TokenCounter 等）
- EnginePort.context_assemble 是透传的
- 没有可复用的抽象

## Goals / Non-Goals — 目标 / 非目标

**目标：**
- 定义包含三个核心方法的 `ContextEngine` ABC
- 提供 `InMemoryContextEngine` 用于测试和单机部署
- 定位为引擎内部的 ABC（不在 EnginePort 上）
- 为 P3 重构（ConversationService 委托给 ContextEngine）做准备

**非目标：**
- 重构 ConversationService（P3）
- 分布式 context engine（P4+）
- 替换 EnginePort.context_assemble（它将变为调用 ContextEngine 的透传）

## Decisions — 设计决策

### D1：ContextEngine 是引擎内部的，不在 EnginePort 上

**选择**：创建 `engine/context.py`，与 `engine/eventstore.py` 并列。

**理由**：Context management is an engine concern (message selection, compression), not a service boundary. It doesn't belong on EnginePort.

### D2：三个方法的最小接口

**选择**：`select_messages(history, budget) -> list[dict]`、`compress(messages) -> list[dict]`、`estimate_tokens(messages) -> int`。

**理由**：覆盖 ConversationService 当前进行的三个上下文操作。数据类参数推迟到 P3（当有实际实现需要时）。

### D3：InMemoryContextEngine 使用启发式方法

**选择**：`select_messages` 保留预算内最新的消息。`compress` 移除超出阈值的最旧消息。`estimate_tokens` 使用基于字符的估算（`len(text) // 4`）。

**理由**：对于 P2 测试和开发来说足够好。P3 添加了实际的 tokenizer 集成。

### D4：预算语义

**选择**：`budget` 是以 token 为单位的 int。当历史记录超过预算时，移除最旧的消息（保留最新的）。如果单条消息超过预算，它仍被包含（ConText 至少返回一条消息）。

**理由**：匹配常见 LLM 上下文窗口行为。逐条消息移除而不是逐 token 移除——更简单，且对于基于消息的上下文来说通常足够。

## Risks / Trade-offs — 风险与权衡

| 风险 | 缓解措施 |
|------|---------|
| 基于字符的 token 估算与模型 tokenizer 不匹配 | 记录此限制；P3 添加可配置的 tokenizer |
| 预算内单条消息仍可能超过模型的实际限制 | 可接受——llm_service 的 max_tokens 处理截断 |
| InMemoryContextEngine 不适合生产 | 它设计用于测试和单机部署；生产使用分布式实现 |