## Why

Hecate's current multi-agent support covers static graph patterns (sequential, fan-out, conditional, broadcast, reflection) and basic agent invocation/handoff. However, agents cannot **communicate dynamically at runtime**, **negotiate outcomes**, **allocate tasks to the best-fit agent**, or **invoke other agents as tools with controlled permissions**. These capabilities are essential for P2's multi-agent orchestration goal and are prerequisites for P3's Distributed Team Orchestration (13.15) and A2A Protocol (2.10).

Research across 11+ platforms (AutoGen, LangGraph Swarm, CrewAI, JiuwenSwarm, Deer-flow, Claude Code, Google A2A, AgentScope, Coze) confirms these four capabilities as the standard multi-agent collaboration primitives.

## What Changes

- **EventBus (2.3a)**: New `EventBus` ABC with `publish`/`subscribe`/`unsubscribe` methods for real-time pub/sub messaging between agents within a session. `InMemoryEventBus` implementation using `asyncio.Queue`. New `CollaborationEvent` type extending the existing `EventType` enum with agent-specific events (AGENT_MESSAGE, AGENT_REQUEST, AGENT_RESPONSE, TASK_ASSIGNED, TASK_COMPLETED, NEGOTIATION_PROPOSAL, NEGOTIATION_ACCEPT, NEGOTIATION_REJECT). Integration with PregelRuntime's `execution_context`.

- **Negotiation Templates (2.3b)**: New graph template factory functions (`build_negotiation_graph`, `build_debate_graph`) following the existing template pattern in `engine/templates.py`. Negotiation uses EventBus for inter-agent message passing within the graph's superstep loop. Templates produce standard `GraphConfig` instances compatible with `GraphCompiler`.

- **TaskAllocator (2.3c)**: New `TaskAllocator` ABC with `SemanticTaskAllocator` implementation that uses LLM-based semantic matching to select the best-fit agent from a pool of candidates. Interface reserves `create_if_not_found` flag for P3 dynamic agent creation (JiuwenSwarm `spawn_member` pattern).

- **Agent-as-Tool (2.3d)**: New `AgentDefinition` dataclass specifying per-invocation agent configuration (tool whitelist + blacklist dual-track following Deer-flow's `SubagentConfig` pattern, model override, context isolation mode, max turns, timeout). New `AgentTool` class that wraps an `AgentDefinition` as a callable tool, integrating with the existing tool execution infrastructure via `EnginePort.agent_execute()`.

## Capabilities

### New Capabilities
- `event-bus`: Real-time publish/subscribe event bus for inter-agent communication within a session — EventBus ABC, InMemoryEventBus, CollaborationEvent types, PregelRuntime integration
- `negotiation-templates`: Graph templates for multi-agent negotiation and debate patterns — build_negotiation_graph, build_debate_graph factory functions
- `task-allocator`: Abstract task allocation with LLM-based semantic matching — TaskAllocator ABC, SemanticTaskAllocator implementation
- `agent-tool`: Agent-as-Tool capability with controlled permissions — AgentDefinition dataclass, AgentTool class, whitelist/blacklist dual-track tool control

### Modified Capabilities
- `engine-types`: Add COLLABORATION_EVENT string enum values to support EventBus-specific event types alongside existing EventType
- `engine-ports`: Extend `agent_execute()` contract to accept optional `AgentDefinition` override parameters (tool filter, context mode, model override)
- `agent-invocation`: Extend AGENT node config to support `invocation_mode: "tool"` with AgentDefinition-based permission scoping (refines existing spec)

## Impact

- **Engine layer**: New files `engine/eventbus.py`, `engine/negotiation.py`, `engine/task_allocator.py`, `engine/agent_tool.py`. Modifications to `engine/types.py` (new event types), `engine/pregel.py` (EventBus integration in execution_context), `engine/templates.py` (new template exports).
- **Services layer**: Service adapter updates to support `agent_execute()` with AgentDefinition overrides.
- **API layer**: No changes — all capabilities are engine-layer primitives exposed through existing execution paths.
- **Dependencies**: No new external dependencies. LLM-based semantic matching reuses existing `llm_invoke()` via EnginePort.
- **P3 evolution path**: EventBus ABC supports future `RedisEventBus` implementation. TaskAllocator ABC reserves `create_if_not_found` for dynamic agent creation. Both are documented in design.md as P3 extension points.
