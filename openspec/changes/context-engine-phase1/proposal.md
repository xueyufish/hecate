## Why

ContextEngine is the last unwired engine ABC (11th of 11). Its three methods (select_messages, compress, estimate_tokens) are defined and tested but never called during graph execution. As a result, LLMWorker passes the full, unbounded message history to the LLM on every superstep — no token budget enforcement, no compression, no message selection. Long conversations will exceed model context windows with no mitigation.

Research across 12 platforms (LangGraph, Google ADK, AutoGen, Semantic Kernel, Claude Code, AgentScope, IBM watsonx, Salesforce, openJiuwen, OpenAI, CrewAI) confirms that **zero platforms** place context filtering at the graph executor level. All place it at the agent/worker level or pre-LLM flow level. This change follows that consensus: PregelRuntime owns the ContextEngine instance (composition root), LLMWorker applies it before LLM invocation.

## What Changes

- PregelRuntime constructor accepts an optional `context_engine: ContextEngine | None` parameter
- PregelRuntime passes ContextEngine to Workers via `execution_context["context_engine"]`
- LLMWorker retrieves ContextEngine from execution_context and applies a 4-step context pipeline before LLM invocation:
  1. Tool result truncation (cap oversized tool outputs)
  2. Token estimation (check against budget)
  3. Message selection (if over budget)
  4. Compression (if still over budget)
- Context pipeline is **non-destructive**: only affects the messages passed to `port.llm_invoke()`. Channel state, snapshot, and checkpoint remain unchanged
- Budget priority: `node_config["max_tokens"]` → runtime `context_budget` → default 8000
- Both `execute()` and `execute_stream()` paths in LLMWorker apply the pipeline
- Backward compatible: `context_engine=None` preserves current behavior (no filtering)

## Capabilities

### New Capabilities

(none)

### Modified Capabilities

- `context-engine`: Add requirements for ContextEngine integration into the execution pipeline — PregelRuntime ownership, execution_context passing, LLMWorker application, non-destructive semantics, budget resolution

## Impact

- **Engine layer**: `engine/pregel.py` (constructor + execution_context), `engine/workers/llm_worker.py` (context pipeline in execute + execute_stream)
- **Service layer**: `services/workflow/execution_service.py` (pass ContextEngine when constructing PregelRuntime), `services/workflow/test_runner.py` (same)
- **Engine subgraph**: `engine/subgraph.py` (pass ContextEngine to sub-runtime)
- **Tests**: New tests for context pipeline behavior, budget enforcement, non-destructive semantics, backward compatibility
- **No breaking changes**: All existing code paths work unchanged when context_engine is None
