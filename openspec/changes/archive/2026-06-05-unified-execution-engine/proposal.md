## Why — 为什么

所有三种 agent 执行模式（chat、three_layer、workflow）使用独立的执行路径：聊天模式直接调用 ConversationService，three_layer 通过测试运行器仅使用预构建的图模板，workflow 通过测试运行器仅使用 PregelRuntime。这种重复意味着每个新功能（流式、可观测性、记忆、防护栏）必须实现三次——每条路径一次。引擎层已经将所有模式统一为 GraphConfig + PregelRuntime，但 services/api 层通过将聊天路由到 ConversationService 的命令式 700 行编排循环而不是声明式图引擎来绕过这一点。

## What Changes — 变更内容

- **破坏性变更**：用基于 PregelRuntime 的图执行替代 ConversationService 的编排循环。ConversationService 被删除；其能力（上下文组装、记忆、知识检索、工具执行、流式）成为由 PregelRuntime 节点调用的独立 Workers。
- **破坏性变更**：`POST /v1/chat/completions` 将所有模式路由通过一个统一的执行入口点（`WorkflowExecutionService`），该入口点编译相应的图模板并通过 PregelRuntime 运行。
- 添加生产级 Workers：`_LLMWorker`、`_ToolWorker`、`_KnowledgeWorker`、`_ConditionWorker`、`_AgentWorker`、`_SuggestionWorker`——替代测试运行器中的 `_TestWorker`。
- 实现 `StreamMode.MESSAGES`，通过 PregelRuntime 的 yield 机制实现令牌级 SSE 流式。
- 在 `templates.py` 中添加 `build_chat_graph()` 模板，用于聊天模式（单个 ConversationNode + 可选的 SuggestionNode）。
- 将 three_layer 模式从 `build_three_layer_graph()` 模板迁移到生产执行（目前仅测试运行器使用）。
- 从 `build_three_layer_graph()` 中移除 guard 节点——guard 现在是一个横切 Hook，而不是图节点。
- 将 Guardrail Hooks（PreLLMHook、PostLLMHook、PreToolHook、PostToolHook）集成到 Workers 中，使所有模式自动获得安全保护，而不仅仅是 three_layer。
- 现有的子服务（ContextAssembler、BudgetManager、MemoryServices、LLMService、KnowledgeBaseService、SuggestionService、EvidenceTracker）保持不变——它们由 Workers 调用。

## Capabilities — 能力

### 新能力
- `production-workers`：生产级 Worker 实现（_LLMWorker、_ToolWorker、_KnowledgeWorker、_ConditionWorker、_AgentWorker、_SuggestionWorker），通过 EnginePort 调用现有服务，集成了 Guardrail Hooks
- `workflow-execution-service`：统一执行入口点，接受任何 GraphConfig，编译它，并通过 PregelRuntime 运行，具有正确的 Worker 选择、Hook 注入、检查点和流式
- `token-streaming`：StreamMode.MESSAGES 实现，通过 PregelRuntime 的 AsyncGenerator yield 实现令牌级 SSE 流式
- `chat-graph-template`：build_chat_graph() 模板，为聊天模式 agent 生成单节点或多节点 GraphConfig
- `guard-hook-integration`：将现有的 Guardrail Hooks（PreLLMHook、PostLLMHook、PreToolHook、PostToolHook）集成到 Workers 中作为横切安全机制，从图拓扑中移除 guard

### 修改的能力
- `pregel-runtime`：添加 StreamMode.MESSAGES 支持（执行期间从 Workers yield 令牌）
- `engine-types`：添加 SUGGESTION NodeType；不需要 GUARD 节点类型（guard 是 Hook，不是节点）
- `orchestration-templates`：从 build_three_layer_graph() 中移除 guard 节点；现有模板需要生产级 Worker 集成而非 _TestWorker

## Impact — 影响

- **Services 层**：ConversationService（700 行）被删除；编排逻辑移至图边。所有子服务保留。
- **API 层**：`api/v1/chat.py` 被重写——移除直接 LLM 调用，路由通过 WorkflowExecutionService。
- **引擎层**：最小更改——PregelRuntime 获得 StreamMode.MESSAGES；Worker 基类不变。
- **测试**：所有聊天 API 测试需要更新以验证通过统一路径的行为。现有引擎测试不变。
- **依赖**：无新的外部依赖。
- **迁移**：three_layer 模式 agent 自动使用现有模板；无需数据迁移。
