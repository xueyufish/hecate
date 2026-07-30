## Why — 为什么

ContextEngine 是最后一个未接入的 engine ABC（11 个中的第 11 个）。它的三个方法（select_messages、compress、estimate_tokens）已定义并测试，但在图形执行期间从未被调用。结果，LLMWorker 在每个超步将完整的、无限制的消息历史传递给 LLM——没有 token 预算限制，没有压缩，没有消息选择。长对话将超过模型上下文窗口且没有任何缓解措施。

对 12 个平台（LangGraph、Google ADK、AutoGen、Semantic Kernel、Claude Code、AgentScope、IBM watsonx、Salesforce、openJiuwen、OpenAI、CrewAI）的研究证实，**零个平台**在图形执行器级别放置上下文过滤。所有平台都将其放在 agent/worker 级别或预 LLM 流程级别。此变更遵循这一共识：PregelRuntime 拥有 ContextEngine 实例（组合根），LLMWorker 在 LLM 调用前应用它。

## What Changes — 变更内容

- PregelRuntime 构造函数接受可选的 `context_engine: ContextEngine | None` 参数
- PregelRuntime 通过 `execution_context["context_engine"]` 将 ContextEngine 传递给 Worker
- LLMWorker 从 execution_context 获取 ContextEngine，并在 LLM 调用前应用 4 步上下文管道：
  1. 工具结果截断（限制过大的工具输出）
  2. Token 估算（检查预算）
  3. 消息选择（如果超出预算）
  4. 压缩（如果仍然超出预算）
- 上下文管道是**非破坏性的**：仅影响传递给 `port.llm_invoke()` 的消息。通道状态、快照和检查点保持不变
- 预算优先级：`node_config["max_tokens"]` → 运行时 `context_budget` → 默认 8000
- LLMWorker 中的 `execute()` 和 `execute_stream()` 路径都应用管道
- 向后兼容：`context_engine=None` 保留当前行为（无过滤）

## Capabilities — 能力

### 新增能力

（无）

### 修改的能力

- `context-engine`：为 ContextEngine 集成到执行管道添加需求——PregelRuntime 所有权、execution_context 传递、LLMWorker 应用、非破坏性语义、预算解析

## Impact — 影响

- **Engine 层**：`engine/pregel.py`（构造函数 + execution_context）、`engine/workers/llm_worker.py`（execute + execute_stream 中的上下文管道）
- **Service 层**：`services/workflow/execution_service.py`（构造 PregelRuntime 时传递 ContextEngine）、`services/workflow/test_runner.py`（相同）
- **Engine 子图**：`engine/subgraph.py`（将 ContextEngine 传递给子运行时）
- **测试**：上下文管道行为、预算执行、非破坏性语义、向后兼容的新测试
- **无破坏性变更**：当 context_engine 为 None 时，所有现有代码路径保持不变
