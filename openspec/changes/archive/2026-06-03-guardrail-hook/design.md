## Context

The engine layer (`engine/pregel.py`) executes agent supersteps in a BSP loop — schedule nodes, dispatch workers, apply channel writes, checkpoint. Within each superstep, workers invoke LLM calls and tool executions via `EnginePort`. There is currently no mechanism to intercept, validate, or transform these calls before or after they happen.

A `SecurityMiddleware` exists in `services/security/middleware.py` with `check_input()` / `check_output()` methods that orchestrate LLM Guard scanning and NeMo Guardrails topic control. However, this middleware is never invoked during execution — it is a standalone service with no integration point.

This change creates four independent guardrail hook ABCs at the engine layer, following a composition-over-inheritance design. P2 scope is interface-only: ABCs + `GuardrailResult` dataclass + NoOp implementations + tests. Actual integration into `ConversationService` and `PregelRuntime` is P3 (features 9.1a/9.1b).

Industry reference: OpenAI Agents SDK uses 4 independent guardrail types (`InputGuardrail`, `OutputGuardrail`, `ToolInputGuardrail`, `ToolOutputGuardrail`) with `tripwire_triggered: bool` semantics. Our design mirrors this separation but uses `allow/block` instead of boolean for clearer intent.

## Goals / Non-Goals

**Goals:**

- Define four independent hook ABCs: `PreLLMHook`, `PostLLMHook`, `PreToolHook`, `PostToolHook` (composition over inheritance)
- Define `GuardrailResult` with `action` semantics: `allow` (pass through) and `block` (halt with reason)
- Provide `NoOp*` pass-through implementations for each hook type
- Add four optional properties to `EnginePort`: `pre_llm_hooks`, `post_llm_hooks`, `pre_tool_hooks`, `post_tool_hooks`
- Full test coverage for all hook types and result actions

**Non-Goals:**

- `modify` action (data transformation in-flight) — deferred to P3 (feature 9.1b)
- `check_cost_ceiling` hook — deferred to P3 BudgetGovernance (independent feature)
- Integration into `PregelRuntime` superstep loop — P3 (9.1a/9.1b)
- Integration into `ConversationService` LLM/tool call paths — P3
- Adapting existing `SecurityMiddleware` as a guardrail hook implementation — P3
- Streaming-aware hooks (partial output inspection) — P3
- Hook priority/ordering mechanism — P3
- Per-Agent hook registration (vs EnginePort-level) — P3

## Decisions

### D1: Composition over inheritance — four independent ABCs

Four separate ABCs (`PreLLMHook`, `PostLLMHook`, `PreToolHook`, `PostToolHook`) instead of one class with five abstract methods. Each hook type is focused on a single interception point.

**Why:** If you only need input checking, you implement only `PreLLMHook`. No need to stub out unused methods. This matches the OpenAI Agents SDK pattern of independent guardrail types.

**Alternative considered:** Single `GuardrailHook` ABC with 5 abstract methods. Rejected — forces implementers to write stubs for unused hooks, violates composition-over-inheritance.

### D2: Engine layer, not service layer

Hook ABCs live in `engine/guardrail.py`, same level as `eventstore.py`, `scheduler.py`, etc. This follows the established P2 pattern: engine defines the contract, services provide implementations.

**Alternative considered:** Define hooks in `services/security/`. Rejected — hooks are an engine extensibility point, not a security-only concern.

### D3: allow/block only (no modify)

Two actions: `ALLOW` (pass through) and `BLOCK` (halt with reason). The `modify` action (transform data in-flight) is deferred to P3 because it requires per-hook-point type contracts that add complexity without immediate value.

**Why:** OpenAI Agents SDK uses only boolean `tripwire_triggered`. MLflow Gateway uses request/response transformation but at a different architectural layer. P2 keeps it simple.

### D4: Four separate properties on EnginePort

Instead of one `guardrail_hooks` property returning a list, four separate properties: `pre_llm_hooks`, `post_llm_hooks`, `pre_tool_hooks`, `post_tool_hooks`. Each returns a list of the corresponding hook type.

**Why:** Type-safe. Each property returns a specific hook type list, not a generic list. Engine code checks `if port.pre_llm_hooks` before iterating.

### D5: Async-only interface

All hook methods are `async def`. Guardrail implementations may need to call external services (ML models, policy servers, audit logs). Sync hooks would force implementers to manage their own event loop.

## Risks / Trade-offs

- **[No modify action]** → Cannot transform data in P2. Mitigation: P3 adds modify with per-hook-point type contracts. Current design is forward-compatible (GuardrailAction enum can be extended).
- **[Block action propagation]** → When a hook returns `block`, the caller needs a clear error path. Mitigation: `GuardrailResult.reason` provides a human-readable string. P3 integration will define error mapping.
- **[EnginePort property proliferation]** → Four properties instead of one. Mitigation: each is a one-liner returning `[]`, and the type safety is worth the minor verbosity.
