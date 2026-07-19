## Context

Hecate already has all the parts for multi-agent handoff:

- **DSL**: `trigger="handoff"` and `trigger="dynamic_handoff"` are valid edge triggers (`graph-dsl.schema.json`).
- **Parser + Compiler**: `_validate_handoff_edges()` performs cycle detection on both triggers.
- **Helper module**: `services/orchestration/handoff.py` provides `build_handoff_tool_schema`, `inject_handoff_tools`, `validate_handoff_target`, `is_handoff_tool_call`, `create_handoff_worker_result` — all unit-tested.
- **Runtime**: `PregelRuntime._resolve_next_nodes()` (pregel.py:502) already honors `Command(goto=...)`.
- **Precedent**: `invocation_mode="tool"` (agent-as-callable-tool, shipped 2026-07-18) does the inverse operation — registers an agent as a parent tool — using the same `_agent_tools` channel and `AgentDefinition` config.

The chain is broken in the middle: no executor ever calls `inject_handoff_tools` or detects `handoff_to_agent` in the LLM response, so no `Command(goto=...)` is ever produced. The helper module is dead code.

Industry convergence (OpenAI Swarm 2024, OpenAI Agents SDK 2025, Google ADK 2025, LangGraph, AutoGen v0.4+) confirms the design pattern:

1. Handoff is a special tool call (one tool per target, or a single tool with `target` enum).
2. The receiving agent's context engineering is critical — pass full history, fresh context, or summarized context, depending on use case.
3. The tool-call/tool-response pair must remain intact across the handoff boundary, or downstream LLMs see malformed history.

Constraints (from AGENTS.md):

- `engine/` cannot import from `services/`, `api/`, or `models/` (only `jsonschema`).
- All public code requires type annotations; no `as any`, no `@ts-ignore` equivalents.
- `from __future__ import annotations` at the top of every file.
- 250-LOC ceiling per module — handoff.py is currently 180 LOC, leaving room.

## Goals / Non-Goals

**Goals:**

- Make the existing `handoff.py` reachable from the execution path so `Command(goto=...)` is actually produced when the LLM calls `handoff_to_agent`.
- Support three context-passing strategies (`inherited`, `isolated`, `summarized`) for the downstream agent.
- Maintain valid conversation history (AIMessage + ToolMessage pairing) across handoffs so downstream LLMs don't break.
- Preserve backward compatibility: graphs authored without `handoff.context_mode` continue to work with `inherited` as default.
- Keep the `engine/` layer free of `services/` imports.

**Non-Goals:**

- **No new routing primitives.** The DSL already supports `trigger="handoff"` and `trigger="dynamic_handoff"`; we are wiring, not redesigning.
- **No `OnHandoffHook` in this change.** The proposal mentioned it as optional; defer to a follow-up to keep this change focused.
- **No handoff for CONVERSATION nodes (LLMWorker).** Scope is AGENT nodes only. LLMWorker handoff can follow once AGENT-node handoff is proven.
- **No new UI work.** The React Flow canvas already supports `DynamicHandoffEdge`. Visual editing of `context_mode` is a separate change.
- **No distributed handoff.** Cross-process or cross-tenant handoff is out of scope; this change stays within a single Pregel runtime instance.
- **No tool-call parallelism changes.** Bedrock-style parallel collaborator execution is not in scope; handoffs remain sequential.

## Decisions

### Decision 1: Port-driven handoff (Option B from explore)

**Choice:** `AgentExecutionPort.agent_execute()` owns the handoff lifecycle (inject tool, detect call, return `handoff_to` in result dict). `AgentWorker` translates `handoff_to` into `WorkerResult(command=Command(goto=...))`.

**Alternatives considered:**

- **Option A — move `handoff.py` into `engine/`** so `AgentWorker` can call it directly. Rejected: violates the layering rule (engine is dependency-free) and grows the engine surface for what is fundamentally an orchestration concern.
- **Option C — port injects, worker detects.** Rejected: scatters handoff knowledge across both layers; port knows prep, worker knows routing semantics. Harder to test in isolation.

**Rationale:** Keeps engine pure (project hard rule), concentrates handoff logic in `services/orchestration/` where the existing module already lives. The only contract change is one optional key (`handoff_to`) in the port's result dict.

### Decision 2: Pass `handoff_targets` via `execution_context`

**Choice:** `PregelRuntime._dispatch_node()` (or the equivalent worker dispatch site) inspects outgoing edges and populates `execution_context["handoff_targets"]` with a list of `{node_id, description}` dicts before invoking the worker.

**Alternatives considered:**

- **Pass the full CompiledGraph to workers.** Rejected: workers don't need the whole graph; they need one slice. Larger API surface, harder to audit.
- **Have the port query the graph directly.** Rejected: the port is services-layer and already has too many responsibilities; it shouldn't traverse graph edges.

**Rationale:** Minimal API change (one dict key in `execution_context`), keeps graph internals in the runtime, gives workers exactly what they need.

### Decision 3: Three `context_mode` values, stored in node config

**Choice:** Add optional `handoff` object to AGENT node config in `graph-dsl.schema.json`:

```json
{
  "handoff": {
    "context_mode": "inherited" | "isolated" | "summarized",
    "description": "optional per-node handoff description override"
  }
}
```

Behavior:

- **`inherited`** (default): downstream agent receives the full `messages` channel as-is. Matches OpenAI Swarm default.
- **`isolated`**: downstream agent starts with only the triggering user message and a synthetic system note ("Handed off from {source_agent} for {reason}"). Matches Claude Code subagent.
- **`summarized`**: upstream messages are folded into a single `system` message containing a structured summary (`from`, `intent`, `key_facts`, `open_questions`). Matches OpenAI `nest_handoff_history`.

Implementation: `filter_messages_for_handoff(messages, context_mode, source_node_id, target_node_id)` helper in `handoff.py`. The AgentWorker applies the filter when building `channel_updates` for the handoff WorkerResult.

**Alternatives considered:**

- **Per-edge `context_mode`** (different modes for different targets from the same source). Rejected: combinatorial explosion, low marginal value, no customer request. Can be added later on the edge config if needed.
- **Free-form filter function in Python.** Rejected: not serializable in JSON DSL, breaks the spec/parser contract.

### Decision 4: AIMessage + ToolMessage pairing on handoff

**Choice:** When `AgentWorker` detects a handoff, the resulting `WorkerResult.channel_updates["messages"]` MUST contain exactly:

1. The triggering `AIMessage` (the LLM's tool-call message, re-emitted with original tool_call_id).
2. A synthetic `ToolMessage` with `tool_call_id` matching #1 and content `"Handed off to {target}"`.

No other messages. The downstream agent's LLM provider receives a well-formed conversation.

**Rationale:** LangGraph documents this requirement explicitly. Without pairing, downstream providers (especially OpenAI) return `400` errors or hallucinate completions for the unpaired tool call. The current `create_handoff_worker_result` writes a single `{"role": "assistant", "content": "Transferring to {target}..."}` — this is broken and needs to be fixed as part of this change.

### Decision 5: Per-target handoff tool descriptions

**Choice:** `build_handoff_tool_schema(target_node_ids, descriptions_by_target)` takes an optional `descriptions_by_target: dict[str, str]`. When provided, the tool description includes each target's role: `"Transfer to {target}: {description}"`. When absent, falls back to current generic description.

Source of descriptions:

1. The target AGENT node's `handoff.description` field (if set in node config).
2. The target Agent's `AgentModel.description` (looked up at port-call time).
3. The target AGENT node's `name` (worst case).

**Rationale:** OpenAI Agents SDK `handoff_description`, Google ADK `agent.description`, Salesforce Agentforce `go_to_X description` — all converge on per-target descriptions because LLM routing accuracy depends on it. Generic "Transfer to another agent" descriptions cause misrouting.

### Decision 6: Backward compatibility via defaults

**Choice:** Graphs authored before this change continue to work:

- AGENT nodes without `handoff.context_mode` → default to `inherited`.
- AGENT nodes without `handoff.description` → use target Agent's `description` from AgentModel.
- No `handoff_targets` in `execution_context` (older runtime) → port doesn't inject handoff tool, preserves current no-op behavior.

**Rationale:** Mandatory because the existing DSL + helpers are already in production. No flag day.

## Risks / Trade-offs

- **[Risk] `AgentExecutionPort` becoming a mega-module.** Currently 360 LOC; this change adds ~80 LOC for handoff injection + detection. → **Mitigation:** 250-LOC ceiling; if port exceeds, extract `HandoffExecutor` class in the same module. Watch this metric in PR review.
- **[Risk] `summarized` context_mode needs an LLM call to summarize.** That's extra latency + cost + a potential failure point. → **Mitigation:** Use a cheap model (Haiku-class); cache summaries by message-hash; on failure, fall back to `isolated` mode with a WARNING log. Document this in design notes.
- **[Risk] Tool-call ID provenance.** When the LLM calls `handoff_to_agent`, we need to preserve `tool_call_id` exactly so the pairing works downstream. If the LLM provider returns non-unique IDs (rare but possible), pairing breaks. → **Mitigation:** Generate UUID suffix on collision; log WARNING; never silently drop the ID.
- **[Risk] Test surface grows.** Need new tests for: handoff injection, handoff detection, three context_mode variants, message pairing, backward-compat path. → **Mitigation:** Use parameterized tests; one test fixture per context_mode; mock LLM service returns canned tool calls. Estimate 8-12 new test functions.
- **[Risk] Graphs with cyclic handoff edges.** Already enforced by `_validate_handoff_edges()` (cycle detection) — no new risk. → **Mitigation:** None needed; existing compiler check covers it.
- **[Trade-off] Port result dict grows another optional key (`handoff_to`).** Slight contract widening. → **Mitigation:** Document the key in `AgentExecutionPort.agent_execute()` docstring; add dataclass `AgentExecutionResult` if dict sprawl continues.
- **[Trade-off] `summarized` mode introduces non-determinism.** Two runs of the same handoff may produce slightly different summaries. → **Mitigation:** Accept it (matches industry behavior); document in spec; recommend `inherited` for test assertions.
