## Why

Agents with many tools suffer from context bloat, reasoning errors, and security exposure — the LLM sees every tool on every turn regardless of relevance. A hard platform gate (`available_when`) lets developers conditionally hide tools from the LLM based on runtime context (verification status, user role, task phase, budget), improving reasoning quality, reducing token cost, and enforcing business rules that prompt engineering alone cannot guarantee. 10-platform research shows Salesforce Agentforce uses this exact pattern (`available when` field with expression evaluation), and Dify has an open feature request (#27887) asking for it.

## What Changes

- Add `available_when: str | None` field to `ToolModel` and corresponding Pydantic schemas — declarative expression evaluated per-invocation
- Implement `ToolGateEvaluator` in `engine/tool_gate.py` — expression evaluator with access to runtime context (session, user, phase, budget)
- Wire tool filtering into `LLMWorker.execute()` and `execute_stream()` — filter tool list after extraction from `node_config`, before `context_assemble` and `llm_invoke`
- Expression language: Python-safe subset using `eval()` with restricted namespace (no builtins, no imports) — supports `==`, `!=`, `>`, `<`, `>=`, `<=`, `and`, `or`, `not`, `in`, parentheses
- Context variables available to expressions: `phase`, `budget_remaining`, `user_id`, `user_role`, `session_id`, `turn_index`, plus channel snapshot values
- Soft gate only — filtered tools are hidden from the LLM's tool list; no execution-layer hard block (10-platform consensus: no platform implements hard gate)

## Capabilities

### New Capabilities

- `tool-gating`: Conditional tool visibility based on `available_when` expressions — includes ToolGateEvaluator, expression evaluation semantics, context variable model, and LLMWorker integration

### Modified Capabilities

_(none — tool-registry spec covers execution routing, not visibility; the ToolModel field addition is an implementation detail)_

## Impact

- **Models**: `ToolModel` gains `available_when` column (nullable, backwards-compatible migration)
- **Engine**: New `engine/tool_gate.py` — zero external deps, follows existing engine ABC pattern
- **Workers**: `LLMWorker.execute()` and `execute_stream()` gain `_filter_tools()` call — one new line per method
- **API**: Tool CRUD schemas gain optional `available_when` field
- **Tests**: New `tests/test_engine/test_tool_gate.py` — evaluator tests, context variable tests, LLMWorker integration tests
- **Dependencies**: None — uses Python built-in `eval()` with restricted namespace
