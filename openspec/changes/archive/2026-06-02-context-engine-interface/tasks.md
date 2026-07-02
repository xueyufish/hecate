## 1. ContextEngine ABC

- [x] 1.1 Create `src/hecate/engine/context.py` with `ContextEngine(ABC)` defining abstract methods: `select_messages(history: list[dict], budget: int) -> list[dict]`, `compress(messages: list[dict]) -> list[dict]`, `estimate_tokens(messages: list[dict]) -> int`
- [x] 1.2 Add full docstrings to ContextEngine ABC and each abstract method

## 2. InMemoryContextEngine

- [x] 2.1 Implement `InMemoryContextEngine(ContextEngine)` with simple heuristics
- [x] 2.2 `select_messages` keeps the most recent messages that fit within token budget
- [x] 2.3 `compress` removes oldest messages when count exceeds threshold (default 50)
- [x] 2.4 `estimate_tokens` uses character-based estimation (len(text) // 4)
- [x] 2.5 Handle edge cases: empty list, zero budget, messages with None content
- [x] 2.6 Add docstrings

## 3. Tests

- [x] 3.1 Create `tests/test_engine/test_context.py`
- [x] 3.2 Test ContextEngine is abstract (cannot instantiate directly)
- [x] 3.3 Test InMemoryContextEngine.select_messages returns recent messages within budget
- [x] 3.4 Test InMemoryContextEngine.select_messages with empty list returns empty
- [x] 3.5 Test InMemoryContextEngine.select_messages with zero budget returns empty
- [x] 3.6 Test InMemoryContextEngine.compress reduces message count
- [x] 3.7 Test InMemoryContextEngine.estimate_tokens returns reasonable estimate
- [x] 3.8 Test InMemoryContextEngine.estimate_tokens with empty list returns 0

## 4. Verification

- [x] 4.1 Run `ruff check src/hecate/engine/context.py tests/test_engine/test_context.py`
- [x] 4.2 Run `ruff format --check src/hecate/engine/context.py tests/test_engine/test_context.py`
- [x] 4.3 Run `mypy src/hecate/engine/context.py`
- [x] 4.4 Run `python -m pytest tests/test_engine/test_context.py -v`
- [x] 4.5 Run full test suite `python -m pytest tests/ -q` to verify no regressions
