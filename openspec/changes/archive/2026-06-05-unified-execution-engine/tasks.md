## 1. Engine Foundation — 引擎基础

- [x] 1.1 在 `engine/types.py` 中的 `NodeType` 枚举中添加 `SUGGESTION`
- [x] 1.2 在 `web/src/lib/workflow-types.ts` 中的 `NodeTypeSchema` 添加 `suggestion`，并在 `dsl-bridge.ts` 中添加节点类型标签
- [x] 1.3 更新 `graph-dsl.schema.json` 以在节点类型枚举中包含 `suggestion`
- [x] 1.4 在 `PregelRuntime.execute()` 中实现 `StreamMode.MESSAGES`——检测流式 Workers，在超步结果之前 yield `{"type": "message", "content": token}` 事件
- [x] 1.5 扩展 `Worker.execute()` 以支持 AsyncGenerator 返回类型（用于流式 Workers），同时保留现有的协程返回

## 2. Production Workers — 生产级 Workers

- [x] 2.1 实现 `_ConditionWorker`——根据通道状态评估表达式，将 `_route` 写入 channel_updates
- [x] 2.2 实现 `_VariableSetWorker`——从配置中读取 variable_name 和 value，写入 channel_updates
- [x] 2.3 实现 `_KnowledgeWorker`——从消息中提取查询，通过 EnginePort 调用 knowledge_base_service，将 context 和 messages 写入 channel_updates
- [x] 2.4 实现 `_ToolWorker`——从通道消息中解析 tool_calls，调用 PreToolHook，执行工具，调用 PostToolHook，捕获证据，将工具结果消息写入 channel_updates
- [x] 2.5 实现 `_AgentWorker`——通过配置中的 agent_id 解析子 agent，提取父通道上下文（messages、variables）作为 initial_input，调用 WorkflowExecutionService 进行嵌套图执行（非直接 agent_execute），将子 agent 响应写入 channel_updates
- [x] 2.6 实现 `_SuggestionWorker`——调用 SuggestionService 生成开场白或跟进建议，将 content 和 suggested_questions 写入 channel_updates
- [x] 2.7 实现 `_LLMWorker`（非流式）——调用 PreLLMHook、上下文组装、记忆加载、压缩、知识检索、LLM 调用、PostLLMHook、证据跟踪，返回带有 messages 和可选 `_has_tool_call` 的 WorkerResult
- [x] 2.8 实现 `_LLMWorker`（流式）——调用 PreLLMHook，通过 AsyncGenerator yield 令牌用于 StreamMode.MESSAGES，调用 PostLLMHook，返回最终的 WorkerResult

## 3. Chat Graph Template — 聊天图模板

- [x] 3.1 在 `engine/templates.py` 中实现 `build_chat_graph()`——CONVERSATION 节点、CONDITION 节点（check_tools）、TOOL_CALL 节点、可选的 SUGGESTION 节点、工具循环的循环边
- [x] 3.2 为 `build_chat_graph()` 编写测试——验证节点数量、边拓扑、通道定义、入口点

## 4. Guard Hook Integration — Guard Hook 集成

- [x] 4.1 从 `build_three_layer_graph()` 中移除 guard 节点——将入口点从 "guard" 改为 "planner"，移除 guard NodeConfig 和 guard→planner 边
- [x] 4.2 更新现有的 three_layer 测试以反映 guard 节点移除（入口现在是 "planner"）
- [x] 4.3 向 Worker 构造函数添加 Hook 参数——`_LLMWorker(pre_llm_hook, post_llm_hook)`、`_ToolWorker(pre_tool_hook, post_tool_hook)`，默认使用 NoOp 变体
- [x] 4.4 将 PreLLMHook 接入 `_LLMWorker`——在 LLM 调用前调用，在 BLOCK 时返回拒绝消息
- [x] 4.5 将 PostLLMHook 接入 `_LLMWorker`——在 LLM 响应后调用，在 BLOCK 时替换响应
- [x] 4.6 将 PreToolHook 接入 `_ToolWorker`——在工具执行前调用，在 BLOCK 时返回阻止原因
- [x] 4.7 将 PostToolHook 接入 `_ToolWorker`——在工具执行后调用，在 BLOCK 时净化结果

## 5. WorkflowExecutionService — 工作流执行服务

- [x] 5.1 在 `services/workflow/execution_service.py` 中创建 `WorkflowExecutionService` 类——接受 AgentModel，按模式解析图模板，编译，向 Workers 注入 Guardrail Hooks，运行 PregelRuntime
- [x] 5.2 实现聊天模式路径——调用 `build_chat_graph()`，将会话元数据注入 initial_input，运行 PregelRuntime
- [x] 5.3 实现 three_layer 模式路径——调用 `build_three_layer_graph()`（无 guard 节点），注入元数据，运行 PregelRuntime
- [x] 5.4 实现 workflow 模式路径——从数据库加载 WorkflowVersionModel，调用 `parse_graph()`，注入元数据，运行 PregelRuntime
- [x] 5.5 实现流式执行——返回将 PregelRuntime MESSAGES 事件映射到 SSE 格式字典的 AsyncGenerator
- [x] 5.6 实现非流式执行——消费 PregelRuntime 生成器，从通道状态提取最终响应

## 6. API Migration — API 迁移

- [x] 6.1 重写 `api/v1/chat.py` 中的 `_process_chat()`——用 `WorkflowExecutionService.execute()` 替代 ConversationService 调用
- [x] 6.2 将 PregelRuntime 流式事件映射到 SSE 格式——`{"type": "message"}` → ChatCompletionChunk, `{"type": "values"}` → 最终响应, `{"type": "interrupt"}` → 中断处理
- [x] 6.3 将 PregelRuntime 非流式结果映射到 ChatCompletionResponse 格式
- [x] 6.4 在响应映射中处理来自通道状态的 citations、suggested_questions 和 annotations
- [x] 6.5 验证 session_lock_manager 集成在新的执行路径下仍能工作

## 7. Cleanup — 清理

- [x] 7.1 从 `services/conversation.py` 中删除 ConversationService 类——验证所有导入已移除（为向后兼容保留，更新引用）
- [x] 7.2 更新 `services/orchestration/agent_execution_port.py` 以移除 ConversationService 依赖
- [x] 7.3 更新 `services/workflow/test_runner.py` 以使用生产级 Workers 替代 `_TestWorker`
- [x] 7.4 从 test_runner 中移除 `_TestWorker` 类（生产级 Workers 现在服务此目的）（保留——mock 模式仍需要）
- [x] 7.5 更新 `engine/guardrail.py` 和 `engine/context.py` 中 ConversationService 的引用

## 8. Tests — 测试

- [x] 8.1 测试 `_LLMWorker`——mock EnginePort，验证上下文组装、记忆加载、LLM 调用、channel_updates 输出
- [x] 8.2 测试 `_LLMWorker` guard hooks——PreLLMHook 阻止、PostLLMHook 阻止、两者都允许
- [x] 8.3 测试 `_ToolWorker`——验证工具调用解析、执行、证据捕获、错误处理
- [x] 8.4 测试 `_ToolWorker` guard hooks——PreToolHook 阻止危险工具、PostToolHook 净化结果
- [x] 8.5 测试 `_ConditionWorker`——验证 has_tool_call、分类匹配、默认回退的表达式评估
- [x] 8.6 测试 `_SuggestionWorker`——验证开场白生成、跟进建议生成
- [x] 8.7 测试 `WorkflowExecutionService` 聊天模式——端到端 mock：AgentModel → build_chat_graph → compile → PregelRuntime → response
- [x] 8.8 测试 `WorkflowExecutionService` three_layer 模式——端到端 mock，包含工具循环（无 guard 节点）
- [x] 8.9 测试 `StreamMode.MESSAGES`——验证从流式 Workers yield 的令牌事件
- [x] 8.10 回归测试 `POST /v1/chat/completions`——流式和非流式，有/无工具/kb/建议
- [x] 8.11 更新引用 `_TestWorker` 的现有引擎测试——替换为生产级 Worker mock
