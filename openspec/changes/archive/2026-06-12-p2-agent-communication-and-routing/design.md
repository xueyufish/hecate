## Context

Hecate's P2 Multi-Agent Orchestration is 95% complete (60/63). The engine already supports:
- 6 collaboration patterns (SEQUENTIAL, PARALLEL, HANDOFF, BROADCAST, NEGOTIATION, DEBATE)
- CONDITION nodes with expression-based routing and multi-key conditional edges
- Handoff edges with cycle detection and auto-injected `handoff_to_agent` tool
- Channel types (LAST_VALUE, TOPIC, ACCUMULATOR) with ChannelBehavior ABC
- Per-node `channels: { readable: [...], writable: [...] }` config in Graph DSL schema
- Canvas UI with edge type differentiation (default, handoff, conditional, fan-out)
- ChannelSelector component for read/write toggle per channel

What's missing: channel access is declared but never enforced, and routing is limited to static expression evaluation. Enterprise platforms provide intent-based and dynamic LLM-driven routing.

## Goals / Non-Goals

**Goals:**
- Enforce channel access boundaries at compile time (soft validation: warn, not block)
- Add runtime warning when a node accesses a channel outside its declared scope
- Enhance ChannelSelector UX with broadcast mode visualization and access summary
- Extend CONDITION node with 3 routing modes: condition (exists), intent (new), dynamic (new)
- Support dynamic handoff edges where LLM selects target at runtime
- Maintain backward compatibility — existing graphs with no routing_mode continue to work

**Non-Goals:**
- Hard channel isolation (raising errors on unauthorized access) — too breaking for existing graphs
- New node types for routing — condition node extension is sufficient
- LLM call caching or batching for dynamic routing — optimization for later
- Custom routing function support (like AutoGen's selector_func) — out of scope for P2
- Changes to the collaboration pattern inference logic — patterns are orthogonal to routing modes

## Decisions

### D1: Combined 2.7b + 2.7c into single change

**Decision**: Implement both features together.
**Rationale**: Same domain (multi-agent coordination), shared canvas config panel infrastructure, combined S+M effort is reasonable for one change.
**Alternative**: Separate changes would duplicate canvas config panel work and risk inconsistent DSL schema changes.

### D2: Channel access approach — soft validation (Option B)

**Decision**: Compile-time check logs warnings for channel access violations. Runtime logs warnings when accessing undeclared channels. Neither blocks execution.
**Rationale**: Hard isolation (raising errors) would break existing graphs that don't declare channel access. Purely declarative (no runtime check) provides no value. Soft validation gives users visibility without breaking anything.
**Alternative A (hard isolation)**: Raise `GraphValidationError` on violations. Too breaking.
**Alternative C (declarative only)**: Validate schema only, no runtime enforcement. No actual protection.

### D3: Routing modes extend existing CONDITION node

**Decision**: Add `routing_mode` field (default: "condition") and `routing_config` field to CONDITION node config. No new node types.
**Rationale**: Routing is a variant of conditional branching — it belongs on the condition node. Adding a new node type would require schema/compiler/runtime/canvas changes across the board. A field extension is minimal and backward compatible.
**Alternative**: New ROUTER node type. Rejected due to implementation cost and conceptual overlap with CONDITION.

### D4: Dynamic routing uses EnginePort.llm_invoke()

**Decision**: When `routing_mode="dynamic"`, the condition evaluation path calls `EnginePort.llm_invoke()` with a routing prompt to classify the next speaker from candidate agents.
**Rationale**: EnginePort already provides `llm_invoke()` as the standard LLM call interface. Dynamic routing is essentially an LLM classification task — perfect fit.
**Alternative**: Dedicated routing service. Over-engineered for P2; EnginePort is the right abstraction level.

### D5: Dynamic handoff uses existing handoff infrastructure

**Decision**: Add `"dynamic_handoff"` edge trigger. When present, the handoff tool is still auto-injected, but the target list includes all reachable agent nodes from the source. The LLM decides which target to call at runtime.
**Rationale**: Reuses existing handoff cycle detection and tool injection. The only change is that the target parameter is not constrained to a single agent.
**Alternative**: Separate "transfer" tool (Google ADK pattern). Rejected — handoff already does this, just needs multi-target support.

### D6: Intent routing uses pattern matching first, LLM fallback

**Decision**: `routing_mode="intent"` evaluates `intent_patterns` (regex → target) first. If no pattern matches, falls back to LLM classification with `routing_prompt`.
**Rationale**: Pattern matching is fast, free, and deterministic. LLM fallback handles edge cases. Two-tier approach gives users control over common paths while maintaining flexibility.

## Risks / Trade-offs

**[R1] Dynamic routing latency** → Each dynamic routing decision requires an LLM call. Mitigation: Document that dynamic routing adds latency. Users can use intent mode (pattern matching) for fast paths and reserve dynamic for cases that need LLM judgment.

**[R2] LLM routing instability** → LLM may return invalid agent names or inconsistent routing decisions. Mitigation: Validate LLM response against `candidate_agents` list. Fall back to "default" target if response is invalid.

**[R3] Channel access validation false positives** → Compile-time check may warn on legitimate dynamic channel access patterns. Mitigation: Warnings are non-blocking; users can ignore them. Document that dynamic patterns may trigger false warnings.

**[R4] Schema migration for existing graphs** → Adding `routing_mode` and `routing_config` to condition node config changes the DSL schema. Mitigation: Both fields are optional with sensible defaults. Existing graphs compile without changes.

**[R5] EnginePort dependency in condition evaluation** → Dynamic/intent routing introduces LLM calls in the condition evaluation path, which previously was pure computation. Mitigation: Only activated when `routing_mode` is "intent" or "dynamic". Default "condition" mode has zero overhead.

## Open Questions

None — all key decisions pre-approved during explore phase.
