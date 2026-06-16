## Why

All three agent execution modes (chat, three_layer, workflow) use separate execution paths: chat mode calls ConversationService directly, three_layer uses a pre-built graph template via test runner only, and workflow uses PregelRuntime via test runner only. This duplication means every new feature (streaming, observability, memory, guardrails) must be implemented three times — once per path. The engine layer already unifies all modes as GraphConfig + PregelRuntime, but the services/api layer bypasses this by routing chat through ConversationService's imperative 700-line orchestration loop instead of the declarative graph engine.

## What Changes

- **BREAKING**: Replace ConversationService's orchestration loop with PregelRuntime-based graph execution. ConversationService is deleted; its capabilities (context assembly, memory, knowledge retrieval, tool execution, streaming) become independent Workers called by PregelRuntime nodes.
- **BREAKING**: `POST /v1/chat/completions` routes all modes through a unified execution entry point (`WorkflowExecutionService`) that compiles the appropriate graph template and runs it via PregelRuntime.
- Add production-grade Workers: `_LLMWorker`, `_ToolWorker`, `_KnowledgeWorker`, `_ConditionWorker`, `_AgentWorker`, `_SuggestionWorker` — replacing `_TestWorker` in test runner.
- Implement `StreamMode.MESSAGES` for token-level SSE streaming through PregelRuntime's yield mechanism.
- Add `build_chat_graph()` template in `templates.py` for chat mode (single ConversationNode + optional SuggestionNode).
- Migrate three_layer mode from `build_three_layer_graph()` template to production execution (currently test-runner-only).
- Remove guard node from `build_three_layer_graph()` — guard is now a cross-cutting Hook, not a graph node.
- Integrate Guardrail Hooks (PreLLMHook, PostLLMHook, PreToolHook, PostToolHook) into Workers so ALL modes get security protection automatically, not just three_layer.
- Existing sub-services (ContextAssembler, BudgetManager, MemoryServices, LLMService, KnowledgeBaseService, SuggestionService, EvidenceTracker) remain unchanged — they are called by Workers.

## Capabilities

### New Capabilities
- `production-workers`: Production-grade Worker implementations (_LLMWorker, _ToolWorker, _KnowledgeWorker, _ConditionWorker, _AgentWorker, _SuggestionWorker) that call existing services through EnginePort, with integrated Guardrail Hooks
- `workflow-execution-service`: Unified execution entry point that accepts any GraphConfig, compiles it, and runs via PregelRuntime with proper Worker selection, Hook injection, checkpoint, and streaming
- `token-streaming`: StreamMode.MESSAGES implementation for token-level SSE streaming through PregelRuntime's AsyncGenerator yield
- `chat-graph-template`: build_chat_graph() template that produces a single-node or multi-node GraphConfig for chat mode agents
- `guard-hook-integration`: Integrate existing Guardrail Hooks (PreLLMHook, PostLLMHook, PreToolHook, PostToolHook) into Workers as cross-cutting security, removing guard from graph topology

### Modified Capabilities
- `pregel-runtime`: Add StreamMode.MESSAGES support (token-level yield from Workers during execution)
- `engine-types`: Add SUGGESTION NodeType; no GUARD node type needed (guard is a Hook, not a node)
- `orchestration-templates`: Remove guard node from build_three_layer_graph(); existing templates need production Worker integration instead of _TestWorker

## Impact

- **Services layer**: ConversationService (700 lines) deleted; orchestration logic moves to graph edges. All sub-services remain.
- **API layer**: `api/v1/chat.py` rewritten — removes direct LLM calls, routes through WorkflowExecutionService.
- **Engine layer**: Minimal changes — PregelRuntime gains StreamMode.MESSAGES; Worker base class unchanged.
- **Tests**: All chat API tests need updating to verify behavior through unified path. Existing engine tests unchanged.
- **Dependencies**: No new external dependencies.
- **Migration**: three_layer mode agents automatically use existing template; no data migration needed.
