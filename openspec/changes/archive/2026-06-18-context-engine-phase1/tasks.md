## 1. PregelRuntime Constructor + execution_context

- [x] 1.1 Add `context_engine: ContextEngine | None = None` parameter to PregelRuntime.__init__
- [x] 1.2 Store as `self._context_engine`
- [x] 1.3 In `_execution_context()`, inject `ctx["context_engine"] = self._context_engine` when not None
- [x] 1.4 Verify existing tests pass with context_engine=None (backward compatible)

## 2. Tool Result Truncation Helper

- [x] 2.1 Create `_truncate_tool_results(messages: list[dict], tool_result_limit: int) -> list[dict]` helper function in llm_worker.py
- [x] 2.2 Truncate tool/assistant messages with tool_results whose estimated tokens exceed limit; preserve first N tokens; append "[truncated]" indicator
- [x] 2.3 Return a new list (non-destructive); leave original messages unchanged
- [x] 2.4 Unit tests: oversized tool result truncated, small tool result unchanged, multiple tool results in one message, no tool results passthrough

## 3. Budget Resolution Helper

- [x] 3.1 Create `_resolve_budget(node_config: dict, execution_context: dict | None) -> int` helper in llm_worker.py
- [x] 3.2 Priority: node_config["max_tokens"] → execution_context["context_budget"] → 8000
- [x] 3.3 Unit tests: per-node priority, runtime fallback, default fallback, all three absent

## 4. Context Pipeline in LLMWorker.execute()

- [x] 4.1 After extracting messages from snapshot, check execution_context for "context_engine"
- [x] 4.2 When present: resolve tool_result_limit (node_config or default 2000), call _truncate_tool_results
- [x] 4.3 Resolve budget via _resolve_budget
- [x] 4.4 Call context_engine.estimate_tokens(truncated_messages)
- [x] 4.5 If over budget: call context_engine.select_messages(truncated_messages, budget)
- [x] 4.6 If still over budget: call context_engine.compress(selected)
- [x] 4.7 Use filtered messages for context_assemble and llm_invoke; do NOT modify snapshot or channel_updates
- [x] 4.8 When context_engine absent: pass messages unchanged (existing behavior)

## 5. Context Pipeline in LLMWorker.execute_stream()

- [x] 5.1 Apply identical pipeline as execute() (steps 4.1–4.8) in execute_stream()
- [x] 5.2 Ensure streaming tokens correspond to filtered messages
- [x] 5.3 WorkerResult channel_updates must contain only the new assistant message (not filtered history)

## 6. Service Layer Wiring

- [x] 6.1 In services/workflow/execution_service.py, construct InMemoryContextEngine and pass to PregelRuntime constructor
- [x] 6.2 In services/workflow/test_runner.py, same wiring
- [x] 6.3 In engine/subgraph.py, pass parent runtime's context_engine to sub-runtime constructor

## 7. Integration Tests

- [x] 7.1 Test: PregelRuntime with ContextEngine passes it to Worker via execution_context
- [x] 7.2 Test: PregelRuntime without ContextEngine — execution_context has no "context_engine" key
- [x] 7.3 Test: LLMWorker with ContextEngine filters messages when over budget (non-streaming)
- [x] 7.4 Test: LLMWorker with ContextEngine filters messages when over budget (streaming)
- [x] 7.5 Test: LLMWorker without ContextEngine passes full messages (backward compatible)
- [x] 7.6 Test: channel messages unchanged after context pipeline (non-destructive)
- [x] 7.7 Test: checkpoint contains full message history after context pipeline
- [x] 7.8 Test: tool result truncation caps oversized outputs
- [x] 7.9 Test: budget resolution priority (per-node > runtime > default)

## 8. Documentation

- [x] 8.1 Update AGENTS.md ContextEngine row: "🔴 Defined only" → "🟡 LLMWorker pipeline"
- [x] 8.2 Update docs/design/engine-design.md if it has ContextEngine integration notes
