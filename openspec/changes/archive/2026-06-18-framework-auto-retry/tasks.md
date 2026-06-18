## 1. Engine ABC: RetryStrategy + NoRetryStrategy

- [x] 1.1 Create `src/hecate/engine/retry.py` with `RetryStrategy(ABC)` defining abstract methods `should_retry(error: Exception, attempt: int) -> bool` and `get_backoff(attempt: int) -> float`
- [x] 1.2 Implement `NoRetryStrategy(RetryStrategy)` — `should_retry()` always returns False, `get_backoff()` always returns 0.0
- [x] 1.3 Full docstrings on module, RetryStrategy class, NoRetryStrategy class, and all public methods
- [x] 1.4 `from __future__ import annotations` at top, type annotations on all methods

## 2. RetryExecutor Component

- [x] 2.1 Implement `RetryExecutor` class in `engine/retry.py` with constructor `(strategy: RetryStrategy, event_store: Any = None)`
- [x] 2.2 Implement `async execute(func, *args, **kwargs) -> WorkerResult` — non-streaming retry loop: call func, check WorkerResult.error, retry per strategy, sleep get_backoff(), emit EventStore events
- [x] 2.3 Implement `async execute_stream(func, *args, **kwargs) -> AsyncGenerator` — streaming retry loop with `first_token_yielded` flag: retry only before first non-WorkerResult item yielded; propagate errors immediately after
- [x] 2.4 EventStore integration: on each retry, append CUSTOM event with payload `{"event_name": "RETRY", "node_id": ..., "attempt": N, "error_type": ..., "error_message": ..., "backoff_seconds": ...}` when event_store is available
- [x] 2.5 Handle max attempts exhaustion gracefully — return last failed WorkerResult (non-streaming) or raise (streaming after first token)

## 3. PregelRuntime Integration

- [x] 3.1 Add `retry_strategy: RetryStrategy | None = None` constructor parameter to PregelRuntime
- [x] 3.2 Default to `NoRetryStrategy()` when None (backward compatible)
- [x] 3.3 Create `RetryExecutor` instance in constructor using the injected strategy
- [x] 3.4 Wrap non-streaming dispatch path (line ~257-264): route `pool.dispatch()` through `retry_executor.execute()` instead of calling directly
- [x] 3.5 Wrap streaming path (line ~248-255): route `worker.execute_stream()` through `retry_executor.execute_stream()` instead of calling directly
- [x] 3.6 Per-node config merge: read `node_config.get("retry", {})`, merge with global config when building per-node strategy (if node_config has retry override, create a temporary strategy for that node)
- [x] 3.7 Pass execution_context (session_id, superstep, event_store) to RetryExecutor for EventStore observability

## 4. DefaultRetryStrategy in Services Layer

- [x] 4.1 Add `DefaultRetryStrategy(RetryStrategy)` to `services/validation/retry_policy.py`
- [x] 4.2 Constructor params: `max_attempts=3, base_delay=1.0, max_delay=30.0, multiplier=2.0, error_classifier: ErrorClassifier | None = None`
- [x] 4.3 Implement `should_retry()`: check attempt < max_attempts AND error_classifier.is_retryable_exception(error)
- [x] 4.4 Implement `get_backoff()`: `min(base_delay * multiplier**attempt, max_delay) * (0.5 + random.random())` — reuse existing RetryPolicy backoff math
- [x] 4.5 Import RetryStrategy from `hecate.engine.retry`

## 5. Tests — Engine Layer

- [x] 5.1 Test RetryStrategy is not instantiable (abstract methods)
- [x] 5.2 Test NoRetryStrategy.should_retry() always returns False
- [x] 5.3 Test NoRetryStrategy.get_backoff() always returns 0.0
- [x] 5.4 Test RetryExecutor.execute() — success on first attempt (no retry)
- [x] 5.5 Test RetryExecutor.execute() — retryable error then success (1 retry)
- [x] 5.6 Test RetryExecutor.execute() — non-retryable error propagates immediately
- [x] 5.7 Test RetryExecutor.execute() — max attempts exhausted (returns last failed result)
- [x] 5.8 Test RetryExecutor.execute_stream() — stream fails before first token → retry succeeds
- [x] 5.9 Test RetryExecutor.execute_stream() — stream fails after first token → no retry, error propagates
- [x] 5.10 Test RetryExecutor.execute_stream() — no duplicate tokens on retry
- [x] 5.11 Test EventStore CUSTOM event emitted on retry
- [x] 5.12 Test no event emitted when EventStore is None

## 6. Tests — Integration

- [x] 6.1 Test PregelRuntime with default NoRetryStrategy — behavior unchanged (existing tests pass)
- [x] 6.2 Test PregelRuntime with DefaultRetryStrategy — retryable worker error triggers retry
- [x] 6.3 Test PregelRuntime per-node config override — node_config["retry"] overrides global
- [x] 6.4 Test PregelRuntime streaming with retry — pre-token failure retried, post-token failure propagated

## 7. Tests — DefaultRetryStrategy

- [x] 7.1 Test DefaultRetryStrategy.should_retry() with retryable error (mock RateLimitError) returns True
- [x] 7.2 Test DefaultRetryStrategy.should_retry() with non-retryable error (mock AuthenticationError) returns False
- [x] 7.3 Test DefaultRetryStrategy.should_retry() with attempt >= max_attempts returns False
- [x] 7.4 Test DefaultRetryStrategy.get_backoff() returns value in expected jitter range [50%, 150%] of base calculation
- [x] 7.5 Test DefaultRetryStrategy.get_backoff() respects max_delay cap

## 8. Documentation

- [x] 8.1 Update AGENTS.md: add RetryStrategy to Engine ABC inventory table (12th ABC)
- [x] 8.2 Update AGENTS.md: add engine/retry.py to key files table
- [x] 8.3 Verify no engine layer violations (retry.py must not import from services/ or models/)
- [x] 8.4 Run ruff check + ruff format --check + mypy + pytest — all must pass
