## 1. Engine Foundation

- [x] 1.1 Add `SUGGESTION` to `NodeType` enum in `engine/types.py`
- [x] 1.2 Add `suggestion` to `NodeTypeSchema` in `web/src/lib/workflow-types.ts` and node type labels in `dsl-bridge.ts`
- [x] 1.3 Update `graph-dsl.schema.json` to include `suggestion` in node type enum
- [x] 1.4 Implement `StreamMode.MESSAGES` in `PregelRuntime.execute()` — detect streaming Workers, yield `{"type": "message", "content": token}` events before superstep results
- [x] 1.5 Extend `Worker.execute()` to support AsyncGenerator return (for streaming Workers) alongside existing coroutine return

## 2. Production Workers

- [x] 2.1 Implement `_ConditionWorker` — evaluate expression against channel state, write `_route` to channel_updates
- [x] 2.2 Implement `_VariableSetWorker` — read variable_name and value from config, write to channel_updates
- [x] 2.3 Implement `_KnowledgeWorker` — extract query from messages, call knowledge_base_service via EnginePort, write context and messages to channel_updates
- [x] 2.4 Implement `_ToolWorker` — parse tool_calls from channel messages, invoke PreToolHook, execute tools, invoke PostToolHook, capture evidence, write tool result messages to channel_updates
- [x] 2.5 Implement `_AgentWorker` — resolve sub-agent by agent_id from config, extract parent channel context (messages, variables) as initial_input, call WorkflowExecutionService for nested graph execution (NOT direct agent_execute), write sub-agent response to channel_updates
- [x] 2.6 Implement `_SuggestionWorker` — call SuggestionService for opening remarks or follow-up suggestions, write content and suggested_questions to channel_updates
- [x] 2.7 Implement `_LLMWorker` (non-streaming) — invoke PreLLMHook, context assembly, memory loading, compression, knowledge retrieval, LLM invocation, PostLLMHook, evidence tracking, return WorkerResult with messages and optional `_has_tool_call`
- [x] 2.8 Implement `_LLMWorker` (streaming) — invoke PreLLMHook, yield tokens via AsyncGenerator for StreamMode.MESSAGES, invoke PostLLMHook, return final WorkerResult

## 3. Chat Graph Template

- [x] 3.1 Implement `build_chat_graph()` in `engine/templates.py` — CONVERSATION node, CONDITION node (check_tools), TOOL_CALL node, optional SUGGESTION node, cyclic edge for tool loop
- [x] 3.2 Write tests for `build_chat_graph()` — verify node count, edge topology, channel definitions, entry point

## 4. Guard Hook Integration

- [x] 4.1 Remove guard node from `build_three_layer_graph()` — change entry point from "guard" to "planner", remove guard NodeConfig and guard→planner edge
- [x] 4.2 Update existing three_layer tests to reflect guard node removal (entry is now "planner")
- [x] 4.3 Add Hook parameters to Worker constructors — `_LLMWorker(pre_llm_hook, post_llm_hook)`, `_ToolWorker(pre_tool_hook, post_tool_hook)`, defaulting to NoOp variants
- [x] 4.4 Wire PreLLMHook into `_LLMWorker` — call before LLM invocation, return rejection message on BLOCK
- [x] 4.5 Wire PostLLMHook into `_LLMWorker` — call after LLM response, replace response on BLOCK
- [x] 4.6 Wire PreToolHook into `_ToolWorker` — call before tool execution, return block reason on BLOCK
- [x] 4.7 Wire PostToolHook into `_ToolWorker` — call after tool execution, sanitize result on BLOCK

## 5. WorkflowExecutionService

- [x] 5.1 Create `WorkflowExecutionService` class in `services/workflow/execution_service.py` — accept AgentModel, resolve graph template by mode, compile, inject Guardrail Hooks into Workers, run PregelRuntime
- [x] 5.2 Implement chat mode path — call `build_chat_graph()`, inject session metadata into initial_input, run PregelRuntime
- [x] 5.3 Implement three_layer mode path — call `build_three_layer_graph()` (no guard node), inject metadata, run PregelRuntime
- [x] 5.4 Implement workflow mode path — load WorkflowVersionModel from DB, call `parse_graph()`, inject metadata, run PregelRuntime
- [x] 5.5 Implement streaming execution — return AsyncGenerator mapping PregelRuntime MESSAGES events to SSE-format dicts
- [x] 5.6 Implement non-streaming execution — consume PregelRuntime generator, extract final response from channel state

## 6. API Migration

- [x] 6.1 Rewrite `api/v1/chat.py` `_process_chat()` — replace ConversationService calls with `WorkflowExecutionService.execute()`
- [x] 6.2 Map PregelRuntime streaming events to SSE format — `{"type": "message"}` → ChatCompletionChunk, `{"type": "values"}` → final response, `{"type": "interrupt"}` → interrupt handling
- [x] 6.3 Map PregelRuntime non-streaming results to ChatCompletionResponse format
- [x] 6.4 Handle citations, suggested_questions, and annotations from channel state in response mapping
- [x] 6.5 Verify session_lock_manager integration still works with new execution path

## 7. Cleanup

- [x] 7.1 Delete ConversationService class from `services/conversation.py` — verify all imports removed (kept for backward compat, updated references)
- [x] 7.2 Update `services/orchestration/agent_execution_port.py` to remove ConversationService dependency
- [x] 7.3 Update `services/workflow/test_runner.py` to use production Workers instead of `_TestWorker`
- [x] 7.4 Remove `_TestWorker` class from test_runner (production Workers now serve this purpose) (kept — mock mode still needed)
- [x] 7.5 Update `engine/guardrail.py` and `engine/context.py` references to ConversationService

## 8. Tests

- [x] 8.1 Test `_LLMWorker` — mock EnginePort, verify context assembly, memory loading, LLM invocation, channel_updates output
- [x] 8.2 Test `_LLMWorker` guard hooks — PreLLMHook blocks, PostLLMHook blocks, both allow
- [x] 8.3 Test `_ToolWorker` — verify tool call parsing, execution, evidence capture, error handling
- [x] 8.4 Test `_ToolWorker` guard hooks — PreToolHook blocks dangerous tool, PostToolHook sanitizes result
- [x] 8.5 Test `_ConditionWorker` — verify expression evaluation for has_tool_call, category matching, default fallback
- [x] 8.6 Test `_SuggestionWorker` — verify opening remarks generation, follow-up suggestion generation
- [x] 8.7 Test `WorkflowExecutionService` chat mode — end-to-end mock: AgentModel → build_chat_graph → compile → PregelRuntime → response
- [x] 8.8 Test `WorkflowExecutionService` three_layer mode — end-to-end mock with tool loop (no guard node)
- [x] 8.9 Test `StreamMode.MESSAGES` — verify token events are yielded from streaming Workers
- [x] 8.10 Regression test `POST /v1/chat/completions` — streaming and non-streaming, with and without tools/kb/suggestions
- [x] 8.11 Update existing engine tests that reference `_TestWorker` — replace with production Worker mocks
