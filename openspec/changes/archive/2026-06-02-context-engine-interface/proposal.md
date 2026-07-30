## Why — 动机

上下文管理当前是碎片化的：
- ConversationService 拥有 ContextAssembler、TokenCounter、BudgetManager（高层级）
- EnginePort.context_assemble 是透传的（未使用）
- 没有可复用、可测试的上下文操作抽象

ContextEngine ABC 提供了一个干净的基础层，ConversationService 可以委托给它，从而实现：
- 上下文逻辑的独立测试
- 可替换的实现（内存、分布式）
- 在对话和图执行路径上保持一致的内容处理

## What Changes — 变更内容

- 在 `engine/context.py` 中添加 `ContextEngine` ABC，包含方法：`select_messages`、`compress`、`estimate_tokens`
- 添加 `InMemoryContextEngine` 实现（简单的 token 计数、基础压缩）
- P3：重构 ConversationService 以委托给 ContextEngine

## Capabilities — 能力变更

### 新增能力
- `context-engine`: 可插拔的上下文管理接口，用于消息选择、压缩和 token 估算

### 修改的能力
- 无（P2 仅为接口预留）

## Impact — 影响范围

- **新文件**: `src/hecate/engine/context.py`（ABC + InMemoryContextEngine）
- **新测试**: `tests/test_engine/test_context.py`