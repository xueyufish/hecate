## Context

Hecate's ContextEngine ABC (engine/context.py) defines three methods — `select_messages`, `compress`, `estimate_tokens` — with an InMemoryContextEngine implementation. However, neither PregelRuntime nor LLMWorker currently calls these methods. LLMWorker extracts the full `messages` list from the channel snapshot and passes it directly to `port.llm_invoke()` via the pass-through `EnginePort.context_assemble()`. Long conversations grow the TOPIC `messages` channel indefinitely with no token budget enforcement.

Research across 12 platforms (LangGraph, Google ADK, AutoGen, Semantic Kernel, Claude Code, AgentScope, IBM watsonx, Salesforce, openJiuwen, OpenAI Assistants, CrewAI, Microsoft Semantic Kernel) shows that **zero platforms** perform context filtering at the graph executor / runtime level. All place it at one of: per-agent level (AutoGen, Semantic Kernel, AgentScope), pre-LLM flow level (Google ADK, Claude Code, IBM watsonx), or user-defined graph nodes (LangGraph).

The existing design doc (archive/2026-06-02-context-engine-interface/design.md, decision D1) states: "ContextEngine is engine-internal, not on EnginePort. EnginePort.context_assemble remains the public API; it delegates to ContextEngine internally."

## Goals / Non-Goals

**Goals:**
- Wire ContextEngine into the execution pipeline so LLM calls receive budget-filtered messages
- PregelRuntime owns the ContextEngine instance (consistent with Scheduler, Eviction, Optimization ABCs)
- LLMWorker applies a multi-step context pipeline before LLM invocation
- Non-destructive: channel state, snapshot, and checkpoint remain unchanged
- Backward compatible: `context_engine=None` preserves current behavior

**Non-Goals:**
- Processor Chain architecture (Phase 2, feature 4.13, P4)
- Async ContextEngine interface (current sync methods; production LLM-based compression is Phase 2)
- Per-model budget from model registry (Phase 2; Phase 1 uses configurable defaults)
- Context offloading / reload capability (Phase 2, inspired by AgentScope Offloader)
- Round-based windowing (Phase 2, inspired by openJiuwen)
- KV Cache coordination (Phase 2, inspired by openJiuwen)
- Refactoring ConversationService to delegate to ContextEngine (separate change)

## Decisions

### D1: PregelRuntime as composition root, LLMWorker as application point

**Choice**: PregelRuntime receives ContextEngine via constructor parameter; LLMWorker retrieves it from execution_context and applies it before `port.llm_invoke()`.

**Rationale**: All 12 researched platforms place context filtering at the agent/worker level or pre-LLM flow level — never at the graph executor level. PregelRuntime's role is state management and dispatch, not LLM prompt engineering. Putting ContextEngine at PregelRuntime level (modifying snapshot) would also corrupt checkpoints with filtered messages, causing permanent information loss.

**Alternatives considered**:
- **Option A (PregelRuntime modifies snapshot)**: Rejected — no industry precedent; destroys checkpoint integrity; all 12 platforms avoid this.
- **Option C (EnginePort.context_assemble delegates to ContextEngine)**: Deferred — this is the long-term goal but requires service-layer refactor. Phase 1 keeps context_assemble as-is for higher-level context enrichment (memory/knowledge injection), while ContextEngine handles low-level message selection/compression in LLMWorker.

### D2: 4-step context pipeline in LLMWorker

**Choice**: LLMWorker applies four sequential steps before LLM invocation:
1. **Tool result truncation** — Cap oversized tool outputs to `tool_result_limit` tokens (inspired by AgentScope)
2. **Token estimation** — Call `context_engine.estimate_tokens(messages)` to check against budget
3. **Message selection** — If over budget, call `context_engine.select_messages(messages, budget)`
4. **Compression** — If still over budget after selection, call `context_engine.compress(selected)`

**Rationale**: Claude Code's 5-level cascade and AgentScope's three-mechanism approach (compression + truncation + offloading) both demonstrate that progressive, cheapest-first intervention is more effective than single-step compression. Phase 1 implements a simplified 4-step version; Phase 2 will evolve into a full processor chain.

### D3: Budget resolution priority

**Choice**: Token budget resolved in this order:
1. `node_config.get("max_tokens")` — per-node explicit configuration
2. `execution_context.get("context_budget")` — runtime-wide global budget
3. `8000` — sensible default for common models

**Rationale**: AgentScope uses `trigger_ratio × model.context_length` which requires model registry integration. IBM watsonx uses a fixed threshold (default 20000). Phase 1 keeps it simple with configurable defaults; Phase 2 will add per-model budget from a model registry.

### D4: Non-destructive semantics

**Choice**: The context pipeline creates a temporary filtered copy of messages. The channel's `messages` field, the snapshot dict, and all checkpoints retain the complete, unfiltered message history.

**Rationale**: Claude Code L4 (Context Collapse) uses non-destructive projection — original messages never modified, collapse decisions replayed at read time. AgentScope's Offloader preserves compressed content to external storage. AutoGen's ChatCompletionContext returns read-only views. All platforms ensure the source of truth (conversation history) is never destroyed by context filtering.

### D5: execution_context passing pattern

**Choice**: PregelRuntime injects ContextEngine into the `execution_context` dict (which it already builds per-superstep for session_id, superstep, event_store, trace_id, event_bus).

**Rationale**: This follows the established pattern in the codebase — EventStore and EventBus are already passed via execution_context. No constructor changes needed for Workers. Backward compatible — Workers that don't check for ContextEngine are unaffected.

### D6: Tool result limit default

**Choice**: `tool_result_limit` defaults to 2000 tokens. Configurable via `node_config.get("tool_result_limit")`.

**Rationale**: AgentScope defaults to 1000 tokens. IBM watsonx uses a `large_message_threshold` of 50000 tokens. 2000 is a middle ground — large enough to preserve useful tool output, small enough to prevent a single tool result from dominating the context window.

## Risks / Trade-offs

| Risk | Mitigation |
|------|------------|
| Sync ContextEngine methods can't do LLM-based summarization | Phase 1 uses InMemoryContextEngine (heuristic-based, sync). LLM-based compression deferred to Phase 2 with async interface. |
| Token estimation (4 chars/token) is inaccurate | Good enough for budget checks; Phase 2 can add tiktoken or provider-specific counting. InMemoryContextEngine already documents this limitation. |
| Tool result truncation may remove critical data | Truncation preserves the first N tokens (beginning of output). Phase 2 Offloader will allow retrieval of full output. |
| Only LLMWorker benefits; other Workers don't filter | LLMWorker is the only Worker that calls `port.llm_invoke()`. Tool/Code workers don't need context filtering. |
| No per-model budget means default 8000 may be too small for large-context models | Configurable via node_config and runtime parameter. Users can set appropriate budgets. Phase 2 adds model registry integration. |
