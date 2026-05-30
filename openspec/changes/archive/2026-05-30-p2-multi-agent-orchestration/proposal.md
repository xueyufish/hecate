## Why

P2 Workflow Canvas is complete (49/51 tasks), providing a visual DAG editor with 6 node types. However, the canvas currently treats each workflow as an isolated graph — users cannot compose multiple Agents into a collaborative workflow. Multi-Agent orchestration is the killer use case for an Agent platform (AD-7) and the natural next step after the canvas. Without it, Hecate is a single-Agent tool with a visual editor, not a multi-Agent platform.

Per AD-7, all orchestration modes (hierarchical, handoff, pipeline, broadcast, selector, etc.) are unified as Graph templates. P2 scope covers: **Handoff** (the most common pattern — customer service triage, general→specialist routing) and **Multi-Agent visual orchestration** (drag multiple Agents onto canvas, define collaboration topology). Pipeline and Broadcast are deferred to P3.

## What Changes

- **Agent node enhancement**: The existing `NodeType.AGENT` node currently delegates to a sub-graph with mock execution. Enhance it to actually resolve and invoke a configured Agent by ID, with proper context isolation (parent→child state mapping) and result propagation (child→parent).
- **Handoff mechanism**: Introduce a `Command(goto=agent_id)` based control transfer where an Agent can hand off execution to another Agent mid-conversation. The receiving Agent inherits the conversation context and continues. This maps to Swarm-style handoff.
- **AgentTool**: Expose other Agents as callable tools — an Agent can invoke another Agent as a tool call, receive the result, and continue. This enables hierarchical delegation without sub-graph nesting.
- **Multi-Agent canvas support**: Extend the workflow canvas to support dragging multiple Agents from a palette, connecting them with edges, and configuring handoff/delegation relationships. Visual distinction between "invoke as tool" (synchronous, result returns) and "handoff to" (control transfers).
- **Orchestration templates**: Pre-built Graph templates for common multi-Agent patterns — customer service triage (router→specialists), content pipeline (researcher→writer→reviewer), and hierarchical delegation (supervisor→workers).
- **Context isolation**: Each Agent execution gets an isolated context window — system prompt, tools, knowledge bases are scoped to the Agent definition, not inherited from the caller. Shared state flows through Channel mappings.

## Capabilities

### New Capabilities
- `agent-handoff`: Agent-to-Agent control transfer via Command(goto), context handoff with conversation continuity, handoff tool auto-generation for LLM
- `agent-invocation`: Invoke an Agent as a tool call (synchronous delegation), result propagation back to caller, error handling and timeout
- `multi-agent-canvas`: Canvas enhancements for multi-Agent workflows — agent palette, handoff edges, orchestration templates, visual multi-Agent debugging
- `orchestration-templates`: Pre-built Graph templates for common multi-Agent patterns (triage, pipeline, hierarchical)

### Modified Capabilities
- (none — no existing specs require requirement-level changes)

## Impact

- **Engine**: `NodeType.AGENT` worker needs real Agent resolution and execution (currently mock). `Command` already supports `goto` — no change needed.
- **Services**: New `AgentOrchestrationService` for handoff logic, Agent resolution, context mapping. Depends on existing `AgentService` and `ConversationService`.
- **API**: New endpoints for orchestration templates (`GET /api/orchestration-templates`), enhanced Agent node execution in workflow test-runner.
- **Frontend**: Canvas agent palette (list available Agents), handoff edge type (dashed vs solid), orchestration template picker, multi-Agent execution visualization.
- **Database**: No schema changes — uses existing `agents`, `workflows`, `sessions` tables. Agent-to-Agent relationships expressed via Graph DSL edges, not a new junction table.
- **Dependencies**: No new external dependencies. Handoff builds on existing `Command` + `PregelRuntime`. Agent invocation reuses `EnginePort` abstraction.
