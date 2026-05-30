## Context

Hecate's P2 Workflow Canvas is complete with 6 node types (conversation, tool-call, condition, agent, knowledge-retrieval, variable-set) and a visual DAG editor. The engine already supports:

- `NodeType.AGENT` — defined but only has mock execution in `_TestWorker`
- `Command(goto=...)` — engine-level control transfer that overrides normal edge resolution
- `EnginePort` — abstract boundary between engine and services (llm_invoke, tool_execute, knowledge_query, etc.)
- `Worker` / `WorkerPool` — dispatch abstraction for node execution
- `PregelRuntime` — BSP execution loop with interrupt/resume and checkpointing

The missing pieces are:
1. **Real Agent execution**: AGENT nodes need to resolve an AgentModel, build its context, invoke its LLM, and return results
2. **Handoff mechanism**: An Agent should be able to transfer control to another Agent mid-conversation (Swarm-style)
3. **Agent-as-Tool**: Agents should be invocable as tools by other Agents (synchronous delegation)
4. **Canvas multi-Agent support**: Visual tooling for composing multi-Agent workflows

Per AD-7, all orchestration patterns are Graph templates. P2 scope = Handoff + Multi-Agent visual orchestration. Pipeline and Broadcast are P3.

## Goals / Non-Goals

**Goals:**
- G1: AGENT node type performs real Agent execution — resolves AgentModel by ID, builds isolated context (system prompt, tools, knowledge bases), invokes LLM, returns result to parent graph
- G2: Handoff — an Agent can return `Command(goto=target_agent_node_id)` to transfer control; conversation context flows with the handoff
- G3: Agent-as-Tool — expose other Agents as callable tools so an LLM can invoke them via tool calling (hierarchical delegation)
- G4: Orchestration templates — pre-built Graph DSL definitions for common patterns (triage, pipeline, hierarchical)
- G5: Canvas multi-Agent support — agent palette, handoff/delegation edge types, template picker

**Non-Goals:**
- Pipeline pattern (sequential deterministic chain) — deferred to P3
- Broadcast pattern (shared message space) — deferred to P3
- Agent-to-Agent communication protocol (A2A) — P4
- Distributed multi-Agent execution across processes — already designed via WorkerPool, P3 Temporal
- Memory isolation between Agents — uses existing Channel isolation, dedicated L1/L3 memory per Agent is P2 memory system work
- Conflict resolution for concurrent multi-Agent writes — P3

## Decisions

### D1: Agent Execution via EnginePort

**Decision**: Add `agent_execute` method to `EnginePort` for real Agent node execution. The engine calls this port when processing an AGENT-type node.

**Rationale**: Engine has zero external deps by design. Adding Agent resolution logic inside the engine would break the layer boundary. The port pattern keeps the engine clean — it just passes the agent_id and channel snapshot to the port, gets back a WorkerResult.

**Alternative considered**: Direct AgentModel lookup inside a `AgentWorker` class in the engine layer — rejected because it would import SQLAlchemy models into the engine.

**Port method**:
```python
async def agent_execute(
    self,
    agent_id: UUID,
    messages: list[dict],
    channel_snapshot: dict,
    context: dict | None = None,
) -> dict:
    """Execute an agent and return its response.
    
    Returns dict with keys:
    - response: the agent's response message
    - tool_calls: any tool calls made during execution
    - usage: token usage stats
    """
```

### D2: Handoff via Command(goto) + HandoffTool

**Decision**: Handoff is implemented as a special tool (`handoff_to_agent`) that the LLM can call. The tool returns a `Command(goto=target_node_id)`. The Pregel runtime already handles `Command(goto=...)` in `_resolve_next_nodes()`.

**Rationale**: The engine already supports `Command(goto=...)` — no engine changes needed. The handoff tool is auto-injected into the Agent's tool list when the graph has agent nodes connected via handoff edges. This follows the Swarm pattern where handoff is a tool call.

**Flow**:
1. Graph DSL defines an edge with `type: "handoff"` between agent nodes
2. When the source agent's LLM is invoked, a `handoff_to_agent` tool is injected
3. LLM calls `handoff_to_agent(target="specialist")`
4. Worker returns `Command(goto="specialist")`
5. Pregel resolves next node as "specialist", executing that agent node

**Alternative considered**: Handoff as a separate node type — rejected because it would require engine changes and doesn't match the Swarm mental model.

### D3: Agent-as-Tool via Dynamic Tool Registration

**Decision**: Agents can be exposed as tools to other Agents. When configuring an AGENT node with `invocation_mode: "tool"`, the target Agent is registered as a callable tool in the source Agent's tool list. The LLM invokes it like any other tool.

**Rationale**: Hierarchical delegation (parent→child) is the most common multi-Agent pattern. Exposing Agents as tools lets the LLM decide when to delegate, which is more flexible than hardcoded graph edges.

**Tool schema**:
```json
{
  "name": "agent_{agent_name}",
  "description": "Delegate to {agent_name}: {agent_persona}",
  "parameters": {
    "type": "object",
    "properties": {
      "task": {"type": "string", "description": "The task to delegate"}
    }
  }
}
```

**Alternative considered**: Sub-graph nesting (agent node contains a full sub-graph) — deferred to P3 because it requires state mapping between parent and child graphs.

### D4: Context Isolation Strategy

**Decision**: Each Agent execution gets an isolated context. The system prompt, tools, and knowledge bases come from the AgentModel definition. Conversation messages are passed from the parent graph via channel mapping.

**Rationale**: Agents should be self-contained units. A specialist Agent shouldn't see the generalist's system prompt, and vice versa. This matches the "agent as independent entity" mental model.

**Implementation**: 
- `agent_execute` port method receives the agent_id and messages
- Port implementation loads AgentModel, builds isolated context (system_prompt + agent's tools + agent's knowledge bases)
- Messages from parent graph are passed as the conversation history
- Agent's response is returned to the parent graph

### D5: Orchestration Templates as Graph DSL JSON

**Decision**: Orchestration templates are stored as Graph DSL JSON files, served via API. No new DB table needed — templates are static resources bundled with the application.

**Rationale**: Templates are immutable starting points. Users pick a template, the canvas loads it, they customize it. No need for a template management system. Simple and aligned with the "everything is a Graph" principle.

**Templates**:
1. **Customer Service Triage**: Router agent → (billing | technical | general) specialist agents
2. **Content Pipeline**: Researcher → Writer → Reviewer (sequential agent chain)
3. **Hierarchical Supervisor**: Supervisor agent → N worker agents (tool-based delegation)

### D6: Canvas Multi-Agent Enhancements

**Decision**: Extend the existing canvas with agent-specific features:
- Agent palette: sidebar showing available agents to drag onto canvas
- Edge type differentiation: solid = invoke-as-tool (synchronous), dashed = handoff (control transfer)
- Template picker: modal to select and load orchestration templates
- Multi-Agent execution view: show which agent is currently executing during test runs

**Rationale**: Reuse the existing canvas infrastructure. No new canvas component needed — just enhance the agent node, add an agent palette, and differentiate edge rendering.

## Risks / Trade-offs

- **[Risk] Agent execution latency**: Each agent node invokes an LLM, compounding latency. → Mitigation: streaming support per-agent node, test-runner mock mode for development.
- **[Risk] Circular handoff loops**: Agent A hands off to B, B hands off back to A. → Mitigation: Pregel max_supersteps limit (default 100). Add cycle detection in compiler for handoff edges.
- **[Risk] Context explosion**: Multi-agent graphs pass growing conversation history. → Mitigation: context budget per agent node (reuse BudgetManager), L2 compression in P2 memory work.
- **[Trade-off] Sub-graph nesting deferred**: AGENT nodes execute the agent's LLM directly (via port), not as a nested sub-graph. This is simpler but less flexible than full state mapping. → Acceptable for P2; sub-graph nesting is P3.
- **[Trade-off] Templates are static files, not DB-managed**: Cannot create/edit templates via API. → Acceptable for P2; template marketplace is P4.
