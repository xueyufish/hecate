## 1. Core Types and Hook ABCs

- [x] 1.1 Create `src/hecate/engine/guardrail.py` with `GuardrailAction` StrEnum (ALLOW, BLOCK), `GuardrailResult` dataclass (action, reason), four independent ABCs (`PreLLMHook`, `PostLLMHook`, `PreToolHook`, `PostToolHook`), and four NoOp implementations
- [x] 1.2 Verify `engine/__init__.py` remains empty (no re-exports needed)

## 2. EnginePort Extension

- [x] 2.1 Add four guardrail properties to `EnginePort` in `src/hecate/engine/ports.py`: `pre_llm_hooks`, `post_llm_hooks`, `pre_tool_hooks`, `post_tool_hooks`, each returning empty list by default

## 3. Tests

- [x] 3.1 Create `tests/test_engine/test_guardrail.py` covering: GuardrailAction enum (2 members), GuardrailResult allow/block, all 4 ABCs cannot instantiate, all 4 NoOp return ALLOW, custom hooks can block, EnginePort properties default to empty

## 4. Verification

- [x] 4.1 Run `ruff check src/hecate/ tests/` — zero errors
- [x] 4.2 Run `ruff format --check src/ tests/` — zero errors
- [x] 4.3 Run `mypy src/` — zero errors
- [x] 4.4 Run `python -m pytest tests/ -q` — all tests pass
