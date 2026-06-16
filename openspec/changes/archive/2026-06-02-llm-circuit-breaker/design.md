## Context

`LLMService` (`services/llm/service.py`) wraps LiteLLM and supports a linear fallback chain: primary model fails → try each `fallback_models` entry in order. There is no memory across requests — if OpenAI is down, every request still attempts `openai/gpt-4o` first, waits for timeout, then falls back. This is the thundering herd problem.

A `CircuitBreaker` class already exists in `services/validation/retry_policy.py` with the standard CLOSED → OPEN → HALF_OPEN state machine, but it is only used for tool execution validation and is not integrated into LLM calls.

## Goals / Non-Goals

**Goals:**

- Per-prefix circuit breaker that isolates faulty LLM routing prefixes (e.g., `"openai"`, `"anthropic"`, `"bedrock"`) across all requests.
- Skip OPEN prefixes in the fallback chain, reducing latency from unnecessary timeout waits.
- Single-probe HALF_OPEN recovery with `asyncio.Lock` to test if a faulty prefix has recovered.
- Optional `on_state_change` callback hook for future EventStore integration.
- Zero breaking changes to `LLMService` public API.

**Non-Goals:**

- Multi-probe HALF_OPEN recovery (resilience4j style) — unnecessary given fallback chain.
- EventStore integration in this change — deferred to P3 (feature 15.6).
- Metrics / dashboard / alerting — out of scope.
- Per-model (rather than per-prefix) granularity.
- Modifying the existing `CircuitBreaker` in `retry_policy.py`.

## Decisions

### D1: Granularity — per routing prefix, not per model or per provider

The breaker key is extracted from the LiteLLM model name prefix: `model.split("/", 1)[0]`. For short names without a slash (e.g., `"gpt-4o"`, `"claude-3.5-sonnet"`), a static prefix mapping table resolves to the correct prefix.

**Why per-prefix over per-provider:** The prefix IS the fault domain. `anthropic/claude-3.5` and `bedrock/claude-3.5` use the same underlying model but different network paths. If Anthropic API is down, `bedrock/` prefix still works. Using prefix = fault domain avoids false-positive isolation.

**Why per-prefix over per-model:** Too granular. `openai/gpt-4o` and `openai/gpt-4o-mini` share the same API endpoint. If one fails, the other likely fails too. Per-prefix correctly groups them.

### D2: Reuse existing `CircuitBreaker` from `retry_policy.py`

The `CircuitBreaker` in `services/validation/retry_policy.py` already implements CLOSED → OPEN → HALF_OPEN with `failure_threshold`, `recovery_timeout`, `record_success()`, `record_failure()`, and `allow_request()`. It is generic enough to reuse for LLM calls.

**Alternative considered:** Build a new `LLMCircuitBreaker` from scratch. Rejected — the existing one has the right semantics and is already tested. Building a manager layer on top is simpler and avoids duplication.

### D3: HALF_OPEN — single probe with asyncio.Lock

When a prefix breaker enters HALF_OPEN after `recovery_timeout`, only one request is allowed through as a probe. All other concurrent requests skip to fallback. The probe uses `asyncio.Lock` to ensure exclusivity.

**Why single probe (Hystrix style) over multi-probe (resilience4j style):**

- LLM has a fallback chain — probe failure does not impact users (they get a fallback response).
- False recovery is cheap: the next request fails, breaker re-opens immediately.
- Simpler implementation, fewer edge cases (no counters, no windows, no failure rate calculations).

### D4: Integration point — inside `LLMService`, not API layer

The breaker wraps the LiteLLM `acompletion()` call inside `LLMService.chat()` and `chat_stream()`. Before calling LiteLLM, check `breaker.is_open(prefix)`; after the call, record success or failure.

**Alternative considered:** Wrap at the API route level (`api/v1/chat.py`). Rejected — the breaker needs model-level awareness (which prefix to check), which the API layer doesn't have. `LLMService` is the right abstraction boundary.

### D5: Fallback chain filtering

When traversing `fallback_models`, skip any model whose prefix breaker is OPEN. This avoids wasting time on known-faulty prefixes. Example: if `openai` breaker is OPEN and fallback chain is `["openai/gpt-4o-mini", "anthropic/claude-3.5"]`, the first model is skipped immediately.

### D6: State-change callback hook

`CircuitBreakerManager` accepts an optional `on_state_change(prefix: str, old_state: CircuitState, new_state: CircuitState)` callback. Default is `None` (no-op). In P3, this hook will be wired to `EventStore.append()` for operational visibility.

## Risks / Trade-offs

- **[Over-aggressive isolation]** → A brief spike of 429 errors could trip the breaker unnecessarily. Mitigation: `failure_threshold` defaults to 5, requiring 5 consecutive failures before opening. Tunable per deployment.
- **[Short name misclassification]** → A model name like `"gpt-4o"` without prefix could map incorrectly. Mitigation: maintain a prefix mapping table covering common short names; unmapped names default to `"unknown"` prefix (shared breaker).
- **[Lock contention in HALF_OPEN]** → Under extreme concurrency, the probe lock could become a bottleneck. Mitigation: only one request waits for the lock result; all others immediately go to fallback. The lock is held for the duration of a single LLM call (seconds, not milliseconds), but fallback requests are not blocked.
- **[No persistence]** → Breaker state is in-memory only. Process restart resets all breakers to CLOSED. Mitigation: acceptable for P2; P3 can add state persistence via EventStore/Redis if needed.
