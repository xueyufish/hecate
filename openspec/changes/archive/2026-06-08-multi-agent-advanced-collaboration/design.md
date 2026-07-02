## Context

Hecate's engine layer provides graph-based agent orchestration via PregelRuntime (BSP execution loop), a ChannelManager for state, template-based graph construction, and WorkerPool dispatch. Multi-agent support currently includes:

- **AgentWorker** (`engine/workers/agent_worker.py`): Executes AGENT-type nodes via nested graph execution, delegating to either `execution_service` or `port.agent_execute()`.
- **Agent Invocation** (`agent-invocation` spec): `EnginePort.agent_execute()` for sub-agent execution with context isolation.
- **Agent Handoff** (`agent-handoff` spec): `handoff_to_agent` tool injection and `Command(goto=)` for control transfer.
- **Templates** (`engine/templates.py`): `build_chat_graph`, `build_three_layer_graph`, `build_fan_out_pipeline`, `build_conditional_pipeline`, `build_reflection_loop`, `build_sequential_pipeline`, `build_broadcast_pipeline`.
- **EventStore** (`engine/eventstore.py`): Append-only audit log with 11 event types, integrated with PregelRuntime.

What's missing: real-time inter-agent messaging, runtime negotiation, intelligent task allocation, and controlled agent-as-tool invocation. These are the four primitives needed for P2 multi-agent orchestration and P3 Distributed Team Orchestration (13.15).

## Goals / Non-Goals

**Goals:**
- Provide an EventBus ABC for session-scoped pub/sub messaging between agents during graph execution
- Add negotiation and debate graph templates following existing template conventions
- Implement a TaskAllocator ABC with LLM-based semantic matching for best-fit agent selection
- Create an AgentTool that wraps an agent as a callable tool with per-invocation permission control
- Ensure all P2 implementations use in-memory data structures with no external dependencies
- Reserve ABC interfaces for P3 evolution (RedisEventBus, dynamic agent creation)

**Non-Goals:**
- Cross-session or cross-process EventBus (P3 — feature 13.15)
- Distributed agent discovery or registration (P3 — feature 13.15)
- Dynamic agent creation during runtime (P3 — `create_if_not_found=True`)
- A2A Protocol compliance (P3 — feature 2.10)
- UI changes for negotiation/task allocation configuration
- Changes to the existing EventStore (append-only audit log remains unchanged)

## Decisions

### D1: EventBus parallel to EventStore, not extending it

**Decision**: Create a separate `EventBus` ABC alongside the existing `EventStore`. EventStore remains an append-only audit log for observability. EventBus is a real-time pub/sub for agent coordination.

**Rationale**: EventStore's semantics (append, get_events, replay, versioned) are fundamentally different from pub/sub (publish, subscribe, unsubscribe, filter). Combining them would violate single responsibility and complicate both. JiuwenSwarm follows the same separation.

**Alternative considered**: Extending EventStore with subscribe() — rejected because EventStore is append-only by design and subscribers would need to poll.

### D2: EventBus integrated via PregelRuntime execution_context

**Decision**: Add `event_bus: EventBus | None = None` to PregelRuntime's constructor. Pass it to workers via `execution_context` dict alongside existing `event_store`.

**Rationale**: This follows the established pattern for EventStore integration. Workers that need pub/sub (e.g., AgentWorker during negotiation) access EventBus via `execution_context["event_bus"]`. No changes to the Worker ABC signature.

### D3: Negotiation templates produce standard GraphConfig

**Decision**: `build_negotiation_graph()` and `build_debate_graph()` return `GraphConfig` instances, same as all other template functions. They use standard AGENT nodes with EventBus-aware channel configurations.

**Rationale**: GraphConfig → GraphCompiler → PregelRuntime is the established pipeline. Negotiation graphs are just specialized graph topologies — no special runtime support needed. The graph itself encodes the negotiation protocol (round structure, termination conditions, message routing).

### D4: TaskAllocator uses LLM semantic matching, not embedding similarity

**Decision**: `SemanticTaskAllocator` calls `port.llm_invoke()` to analyze task descriptions against candidate agent descriptions, producing a scored ranking. No embedding model dependency.

**Rationale**: Adding an embedding model (e.g., via sentence-transformers) would introduce a new ML dependency and require model management. LLM-based matching reuses the existing `llm_invoke()` port and provides richer semantic understanding. AutoGen's SelectorGroupChat uses the same approach.

**Alternative considered**: Embedding cosine similarity — rejected due to new dependency and lower quality for short agent descriptions.

### D5: Agent-as-Tool uses whitelist + blacklist dual-track (Deer-flow pattern)

**Decision**: `AgentDefinition` specifies `tools: list[str] | None` (whitelist, None=inherit all) and `disallowed_tools: list[str]` (blacklist, default excludes `["agent_execute"]` to prevent nesting).

**Rationale**: Deer-flow (ByteDance) validates this pattern in production. Whitelist-only (Claude Code) is too rigid for enterprise scenarios where different callers need different permissions. Blacklist-only (none of the surveyed platforms) is insufficient for precise control. The dual-track handles both "only these tools" and "all tools except these" scenarios.

**Resolution order**: If `tools` is not None → use whitelist minus blacklist. If `tools` is None → inherit all minus blacklist.

### D6: AgentDefinition is per-invocation, not per-AgentModel

**Decision**: `AgentDefinition` is passed at tool invocation time, not stored on `AgentModel`. The same agent can be invoked with different permission sets by different callers.

**Rationale**: In a multi-agent graph, Agent A may need to call "researcher" with read-only tools, while Agent B calls the same "researcher" with full tool access. Per-AgentModel definitions would be global and inflexible.

### D7: context_mode supports "inherited" and "isolated"

**Decision**: `AgentDefinition.context_mode` field: `"inherited"` (default) shares the parent's messages channel with the sub-agent; `"isolated"` creates a fresh message context for the sub-agent.

**Rationale**: Claude Code uses context isolation by default (subagents start fresh). For Hecate's graph-based execution, inherited context is more common (sub-agents need conversation history), but isolated mode is essential for expert agents that should only see the specific task, not the full conversation.

## Risks / Trade-offs

**[LLM cost for TaskAllocator]** → SemanticTaskAllocator calls LLM on every allocation, adding latency and cost. Mitigation: cache allocation results for identical task descriptions within a session; allow fallback to round-robin allocator for cost-sensitive deployments.

**[EventBus memory usage]** → InMemoryEventBus stores all published events in memory until subscribers consume them. Mitigation: per-topic queue size limits with oldest-drop policy; events are session-scoped and GC'd when session ends.

**[Negotiation graph complexity]** → Negotiation templates produce multi-round graphs that may run for many supersteps. Mitigation: configurable max_rounds parameter in template builders; PregelRuntime's existing max_supersteps guard.

**[Agent-as-Tool recursion]** → Agent A calls Agent B as tool, Agent B calls Agent A as tool. Mitigation: default `disallowed_tools=["agent_execute"]` prevents tool-level nesting; graph compiler's cycle detection prevents handoff-level nesting.

**[EventBus + EventStore confusion]** → Two event systems may confuse developers. Mitigation: clear naming (EventBus for real-time coordination, EventStore for audit trail); EventBus events are not persisted by default.
