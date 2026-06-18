## 1. Engine — ToolGateEvaluator

- [x] 1.1 Create `src/hecate/engine/tool_gate.py` with `ToolGateEvaluator` class
- [x] 1.2 Implement `evaluate(expression: str, context: dict) -> bool` — uses `eval()` with restricted namespace (`__builtins__: {}`), catches all exceptions and returns `False` (fail-closed), logs WARNING on evaluation failure
- [x] 1.3 Implement `filter_tools(tools: list[dict], context: dict) -> list[dict]` — iterates tools, evaluates `available_when` field, returns filtered list; tools without `available_when` or with `None` pass through
- [x] 1.4 `from __future__ import annotations` at top, type annotations on all methods, full docstrings on module/class/public methods
- [x] 1.5 Engine layer constraint: zero imports from services/ or models/

## 2. Models — ToolModel + Schemas

- [x] 2.1 Add `available_when: Mapped[str | None]` to `ToolModel` in `models/tool.py` — nullable column, default None
- [x] 2.2 Add `available_when: str | None = None` to `ToolCreateSchema` and `ToolUpdateSchema`
- [x] 2.3 Add `available_when: str | None = None` to `ToolReadSchema`
- [x] 2.4 Add Alembic migration to add `available_when` column to `tools` table (nullable, backwards-compatible)

## 3. LLMWorker Integration

- [x] 3.1 Add `ToolGateEvaluator` instance to `LLMWorker.__init__` (or create per-call — confirm with existing pattern)
- [x] 3.2 Add private `_filter_tools(tools, execution_context, channel_snapshot) -> list[dict]` method — builds flat context dict from execution_context + channel_snapshot, delegates to `ToolGateEvaluator.filter_tools()`
- [x] 3.3 In `LLMWorker.execute()`: call `self._filter_tools(tools, execution_context, channel_snapshot)` after line 191 (`tools = node_config.get("tools")`) and before PreLLMHook call
- [x] 3.4 In `LLMWorker.execute_stream()`: call `self._filter_tools(tools, execution_context, channel_snapshot)` after line 330 (`tools = node_config.get("tools")`) and before PreLLMHook call
- [x] 3.5 Context dict assembly: merge `session_id`, `superstep` from execution_context; `_user_id` → `user_id`, `_agent_id` → `agent_id`, `_turn_index` → `turn_index` from channel_snapshot

## 4. Tests — ToolGateEvaluator

- [x] 4.1 Test `evaluate()` with simple equality expression returns True/False correctly
- [x] 4.2 Test `evaluate()` with compound `and`/`or` expressions
- [x] 4.3 Test `evaluate()` with membership `in` check
- [x] 4.4 Test `evaluate()` blocks `__import__` access (returns False, no exception propagated)
- [x] 4.5 Test `evaluate()` blocks `__builtins__` access (returns False)
- [x] 4.6 Test `evaluate()` with undefined variable → returns False (fail-closed) + logs WARNING
- [x] 4.7 Test `evaluate()` with syntax error → returns False (fail-closed) + logs WARNING
- [x] 4.8 Test `filter_tools()` with mixed tools (some with available_when, some without)
- [x] 4.9 Test `filter_tools()` with all tools filtered out → empty list
- [x] 4.10 Test `filter_tools()` with empty input → empty list
- [x] 4.11 Test `filter_tools()` preserves tool dict structure (no mutation of original dicts)

## 5. Tests — LLMWorker Integration

- [x] 5.1 Test `execute()` filters tools with `available_when` before passing to `llm_invoke`
- [x] 5.2 Test `execute()` passes tools unchanged when no `available_when` is set (backward compatible)
- [x] 5.3 Test `execute()` PreLLMHook receives filtered tool list
- [x] 5.4 Test `execute_stream()` filters tools identically to `execute()`
- [x] 5.5 Test context dict assembly from execution_context and channel_snapshot
- [x] 5.6 Test missing channel_snapshot keys are omitted from context (fail-closed behavior)

## 6. Tests — Model + Schema

- [x] 6.1 Test `ToolModel` accepts `available_when` field and persists to database
- [x] 6.2 Test `ToolCreateSchema` accepts optional `available_when` string
- [x] 6.3 Test `ToolCreateSchema` without `available_when` defaults to None
- [x] 6.4 Test `ToolReadSchema` includes `available_when` in serialized output

## 7. Documentation

- [x] 7.1 Update AGENTS.md: add `tool_gate.py` to key files table if applicable
- [x] 7.2 Verify engine layer has zero new external dependencies
- [x] 7.3 Run ruff check + ruff format --check + mypy + pytest — all must pass
