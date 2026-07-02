## Why

The engine layer currently has no mechanism to intercept or control individual LLM calls and tool executions within a superstep cycle. Security scanning exists at the API boundary (`SecurityMiddleware`) but is never invoked during execution, and there is no way to inject custom pre/post checks (safety, compliance, cost limits) into the agent runtime without modifying core service code. A guardrail hook interface at the engine level enables pluggable safety, compliance, and observability policies that execute per-step — a prerequisite for P3 AI-driven guardrails (9.1a/9.1b) and multi-agent orchestration.

## What Changes

- Add `GuardrailHook` ABC in `engine/guardrail.py` with five hook points: `on_pre_llm_call`, `on_post_llm_call`, `on_pre_tool_call`, `on_post_tool_call`, `check_cost_ceiling`
- Add `GuardrailResult` dataclass with `action` (allow/block/modify) semantics and optional `data`/`reason` fields
- Add `NoOpGuardrailHook` pass-through implementation for default behavior
- Add `GuardrailHook` optional property to `EnginePort` (same pattern as `event_store`)
- Add unit tests covering all hook methods and result actions

## Capabilities

### New Capabilities
- `guardrail-hook`: Engine-level ABC for per-step safety/compliance hooks with allow/block/modify semantics, five hook points around LLM and tool calls, and a default NoOp implementation

### Modified Capabilities
- `engine-ports`: Add optional `guardrail_hooks` property to `EnginePort` for adapter-level hook registration

## Impact

- **New file**: `src/hecate/engine/guardrail.py` (ABC + GuardrailResult + NoOpGuardrailHook)
- **Modified file**: `src/hecate/engine/ports.py` (add `guardrail_hooks` property)
- **New test file**: `tests/test_engine/test_guardrail.py`
- **No breaking changes**: All additions are optional with default no-op behavior
- **Dependencies**: None — follows same zero-dependency pattern as EventStore, SchedulerStrategy, etc.
