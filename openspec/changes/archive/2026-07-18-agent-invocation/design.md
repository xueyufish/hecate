## Context

Hecate's agent execution has two paths today:

1. **AgentWorker** (engine layer) — handles AGENT-type nodes in the Pregel runtime. Supports two strategies: nested graph execution via WorkflowExecutionService (primary), and port-based execution via `EnginePort.agent_execute()` (fallback).

2. **AgentExecutionPort** (services layer) — concrete EnginePort adapter for agent execution. Currently a thin shell: loads AgentModel from DB, injects persona + skills as system prompt, calls `llm_service.chat(tools=None)`.

The gap: `AgentExecutionPort` bypasses the full LLM pipeline that `LLMWorker` provides (tool loading, knowledge retrieval, guard hooks, context assembly, token budget management). Agents invoked via `agent_execute` get degraded behavior.

Additionally, the Agent-as-Tool capability (`AgentDefinition` + `AgentTool` in `engine/agent_tool.py`) is fully built but lacks a DSL-level `invocation_mode` switch to activate it from graph definitions.

## Goals / Non-Goals

**Goals:**
- Bring AgentExecutionPort to parity with LLMWorker's pipeline (tools, KB, hooks, context assembly)
- Add `invocation_mode` field to AGENT node DSL schema for Agent-as-Tool activation
- Wire `invocation_mode` in AgentWorker to route between nested graph execution and Agent-as-Tool
- Ensure `AgentDefinition.resolve_tools()` filtering works end-to-end in agent_execute
- Add unit tests for AgentExecutionPort

**Non-Goals:**
- Streaming support for agent_execute (spec returns dict, not AsyncGenerator — deferred to P3)
- A2A remote agent execution (already exists in AgentTool.execute_remote — not in scope)
- Agent Handoff changes (already fully implemented in handoff.py)
- Changes to WorkflowExecutionService's nested graph execution path

## Decisions

### Decision 1: Upgrade AgentExecutionPort in-place vs. new class

**Choice**: Upgrade `AgentExecutionPort.agent_execute()` in-place.

**Rationale**: The class already exists, is wired into `_ProductionEnginePort`, and has the right method signature. Creating a new class would require changing the adapter factory and all callers. In-place upgrade is lower risk.

**Alternatives considered**:
- New `FullPipelineAgentExecutionPort` — rejected: unnecessary indirection, same DB session and LLM service dependencies.
- Delegate to LLMWorker internally — rejected: LLMWorker expects a WorkerResult, not a dict. The return type contract differs.

### Decision 2: How to access guard hooks and context engine

**Choice**: Accept optional `pre_hook`, `post_hook`, and `context_engine` parameters in `AgentExecutionPort.__init__()`, defaulting to NoOp variants.

**Rationale**: Follows the same pattern as `LLMWorker.__init__()` which accepts `pre_llm_hook` and `post_llm_hook`. The `_ProductionEnginePort` factory will wire the actual hooks. Defaulting to NoOp maintains backward compatibility.

**Alternatives considered**:
- Pass hooks via `execution_context` dict — rejected: fragile, type-unsafe, couples engine runtime to service layer.
- Global singleton hooks — rejected: violates DI principles, makes testing harder.

### Decision 3: invocation_mode default value

**Choice**: Default `invocation_mode` to `"graph"` (existing behavior).

**Rationale**: All existing AGENT nodes use nested graph execution. Changing the default would break existing graphs. `"tool"` mode is opt-in.

### Decision 4: Where to load agent tools in agent_execute

**Choice**: Load tools from `AgentModel.tool_ids` (or equivalent) within `agent_execute()`, then apply `AgentDefinition.resolve_tools()` filtering if an AgentDefinition is provided.

**Rationale**: The agent's configured tools are the base set. AgentDefinition's whitelist/blacklist narrows them for specific invocations. This matches the existing `AgentTool.resolve_tools()` design.

### Decision 5: Knowledge base integration scope

**Choice**: Call `EnginePort.knowledge_query()` within agent_execute when the agent has knowledge bases configured, and inject results as context messages.

**Rationale**: This brings agent_execute to parity with what CONVERSATION nodes can do. The knowledge_query method already exists on EnginePort and is implemented in AgentExecutionPort.

## Risks / Trade-offs

**[Risk] Increased latency** — Adding tools, KB queries, and hooks adds latency to agent_execute calls.
→ Mitigation: KB queries run in parallel (already implemented). Hook execution is fast (in-memory checks). Tool loading is a single DB query.

**[Risk] Circular dependency** — AgentExecutionPort needs to call EnginePort methods (knowledge_query, context_assemble) but is itself an EnginePort implementation.
→ Mitigation: AgentExecutionPort calls its own methods (self.knowledge_query, self.context_assemble). No circular import — it's the same object.

**[Risk] Breaking existing agent_execute callers** — Upgrading the pipeline could change response format.
→ Mitigation: Response dict keys (response, usage, model) are unchanged. Additional keys are additive. No breaking change.

**[Trade-off] No streaming** — AgentExecute returns a dict, not a stream. Parent agents can't get token-by-token output from sub-agents.
→ Acceptable: Spec defines dict return. Streaming is a separate concern (P3).
