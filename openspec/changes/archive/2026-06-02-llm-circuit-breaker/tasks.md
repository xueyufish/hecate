## 1. Core: CircuitBreakerManager

- [x] 1.1 Create `src/hecate/services/llm/circuit_breaker.py` with `CircuitBreakerManager` class skeleton: `__init__` (failure_threshold, recovery_timeout, on_state_change callback), `_breakers: dict[str, CircuitBreaker]`, `_locks: dict[str, asyncio.Lock]`
- [x] 1.2 Implement `_extract_prefix(model: str) -> str` with slash-based extraction and static short-name mapping table
- [x] 1.3 Implement `get_breaker(prefix: str) -> CircuitBreaker` with lazy creation protected by lock (thread safety for concurrent new-prefix requests)
- [x] 1.4 Implement `is_open(model: str) -> bool` — extract prefix, check breaker state
- [x] 1.5 Implement `record_success(model: str) -> None` — extract prefix, delegate to breaker, invoke on_state_change if state changed
- [x] 1.6 Implement `record_failure(model: str) -> None` — extract prefix, delegate to breaker, invoke on_state_change if state changed
- [x] 1.7 Implement `acquire_probe(prefix: str) -> bool` — acquire asyncio.Lock for HALF_OPEN probe; return False if already held (caller should fallback)
- [x] 1.8 Implement `release_probe(prefix: str) -> None` — release the probe lock

## 2. Integration: LLMService

- [x] 2.1 Add optional `circuit_breaker: CircuitBreakerManager | None = None` parameter to `LLMService.__init__`
- [x] 2.2 Modify `chat()` — before LiteLLM call: check `circuit_breaker.is_open(model)`, if OPEN skip to fallback; if HALF_OPEN, attempt probe with lock
- [x] 2.3 Modify `chat()` — after LiteLLM success: call `circuit_breaker.record_success(model)`
- [x] 2.4 Modify `chat()` — after LiteLLM failure: call `circuit_breaker.record_failure(model)`
- [x] 2.5 Modify `chat_stream()` — same breaker integration as chat(): OPEN→skip, HALF_OPEN→probe, record success/failure
- [x] 2.6 Modify `_try_fallback()` — skip models whose prefix breaker is OPEN; record failure for each failed fallback attempt
- [x] 2.7 Modify `_try_fallback_stream()` — same fallback filtering and failure recording as _try_fallback()

## 3. Feature Catalog

- [x] 3.1 Add P3 feature entry 15.6 "熔断事件集成" to `docs/features/feature-catalog.md` noting dependency on 1.3.10 + 15.1

## 4. Tests

- [x] 4.1 Create `tests/test_services/test_llm/test_circuit_breaker.py` with test file setup (imports, fixtures)
- [x] 4.2 Test `_extract_prefix` — slash-based, short-name mapping, unknown fallback
- [x] 4.3 Test `CircuitBreakerManager` lazy creation and thread safety (concurrent new-prefix)
- [x] 4.4 Test breaker state transitions: CLOSED→OPEN (threshold), OPEN→HALF_OPEN (timeout), HALF_OPEN→CLOSED (success), HALF_OPEN→OPEN (failure)
- [x] 4.5 Test single-probe HALF_OPEN: one request passes, concurrent requests skip to fallback
- [x] 4.6 Test fallback chain filtering: OPEN prefix models are skipped
- [x] 4.7 Test `LLMService.chat()` integration: OPEN→skip, HALF_OPEN→probe, success/failure recording
- [x] 4.8 Test `LLMService.chat_stream()` integration: same as chat() but for streaming path
- [x] 4.9 Test `on_state_change` callback invocation on state transitions
- [x] 4.10 Test `LLMService` without breaker: behavior identical to current implementation (no regression)
- [x] 4.11 Test all prefixes OPEN: RuntimeError("All models failed") is raised

## 5. Verification

- [x] 5.1 Run `ruff check src/hecate/ tests/` — 0 errors
- [x] 5.2 Run `ruff format --check src/ tests/` — 0 errors
- [x] 5.3 Run `mypy src/` — 0 errors
- [x] 5.4 Run `python -m pytest tests/ -q` — all pass
