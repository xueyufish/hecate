## Why

When an LLM provider (e.g., OpenAI) experiences a partial or full outage, every incoming request still attempts to call the primary model first, waits for a timeout, then falls back. This creates a thundering herd effect: all requests pay the timeout penalty before reaching a working fallback model. A circuit breaker per routing prefix isolates faulty prefixes instantly, skipping them and going directly to healthy alternatives.

## What Changes

- Add a `CircuitBreakerManager` in `services/llm/` that maintains per-prefix circuit breakers (keyed by the routing prefix extracted from LiteLLM model names, e.g., `"openai"` from `"openai/gpt-4o"`).
- Reuse the existing `CircuitBreaker` / `CircuitState` from `services/validation/retry_policy.py` as the per-prefix breaker instance.
- Integrate `CircuitBreakerManager` into `LLMService`: check breaker state before calling LiteLLM, record success/failure after each call.
- In HALF_OPEN state, allow a single probe request (with `asyncio.Lock`) to test the faulty prefix; all other requests skip to fallback.
- Filter the fallback chain to skip models whose prefix breaker is OPEN.
- Reserve an `on_state_change` callback hook on `CircuitBreakerManager` for future EventStore integration (P3).
- Add a P3 feature entry (15.6) in `feature-catalog.md` to track EventStore integration.

## Capabilities

### New Capabilities

- `llm-circuit-breaker`: Per-prefix circuit breaker for LLM calls — state machine (CLOSED → OPEN → HALF_OPEN), single-probe recovery, fallback chain filtering, optional state-change callback hook.

### Modified Capabilities

- None. `LLMService` gains an optional dependency; no breaking changes to its public API.

## Impact

- **Code**: `src/hecate/services/llm/service.py` (integrate breaker), new `src/hecate/services/llm/circuit_breaker.py` (manager), `src/hecate/services/validation/retry_policy.py` (no changes, reused as-is).
- **Tests**: New `tests/test_services/test_llm/test_circuit_breaker.py`, update existing LLM service tests to cover breaker integration.
- **Dependencies**: No new external packages. Reuses existing `CircuitBreaker` from `validation/retry_policy.py`.
- **API**: No API changes. Breaker is an internal optimization transparent to callers.
- **Feature catalog**: Add P3 entry 15.6 (circuit breaker EventStore integration) to `docs/features/feature-catalog.md`.
