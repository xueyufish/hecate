## ADDED Requirements

### Requirement: Per-prefix circuit breaker management
The system SHALL maintain a separate `CircuitBreaker` instance per routing prefix extracted from LiteLLM model names. The prefix is the first segment before `"/"` (e.g., `"openai"` from `"openai/gpt-4o"`). For model names without a slash, the system SHALL resolve the prefix via a static mapping table (`gpt→openai`, `claude→anthropic`, `gemini→gemini`, `deepseek→deepseek`). Unmapped names SHALL default to `"unknown"`.

#### Scenario: Prefix extraction with slash
- **WHEN** model name is `"openai/gpt-4o"`
- **THEN** prefix is `"openai"`

#### Scenario: Prefix extraction without slash
- **WHEN** model name is `"gpt-4o"`
- **THEN** prefix is `"openai"` (via mapping table)

#### Scenario: Unknown short name
- **WHEN** model name is `"some-new-model"` and no mapping exists
- **THEN** prefix is `"unknown"`

#### Scenario: Lazy breaker creation
- **WHEN** a prefix is seen for the first time
- **THEN** a new `CircuitBreaker` instance is created for that prefix with default thresholds

### Requirement: Circuit breaker state machine
Each per-prefix breaker SHALL follow the standard three-state machine: CLOSED (requests pass through), OPEN (requests are rejected), HALF_OPEN (one probe request allowed). The breaker SHALL transition from CLOSED to OPEN when `failure_threshold` consecutive failures are recorded. The breaker SHALL transition from OPEN to HALF_OPEN after `recovery_timeout` seconds have elapsed. The breaker SHALL transition from HALF_OPEN to CLOSED on probe success, or back to OPEN on probe failure.

#### Scenario: CLOSED to OPEN on consecutive failures
- **WHEN** 5 consecutive failures (default threshold) are recorded for prefix `"openai"`
- **THEN** breaker state transitions to OPEN

#### Scenario: OPEN to HALF_OPEN after timeout
- **WHEN** breaker is OPEN and `recovery_timeout` (default 30s) has elapsed
- **THEN** breaker state becomes HALF_OPEN

#### Scenario: HALF_OPEN to CLOSED on success
- **WHEN** breaker is HALF_OPEN and a probe request succeeds
- **THEN** breaker state transitions to CLOSED and failure count resets

#### Scenario: HALF_OPEN to OPEN on failure
- **WHEN** breaker is HALF_OPEN and a probe request fails
- **THEN** breaker state transitions back to OPEN and `recovery_timeout` restarts

### Requirement: Single-probe HALF_OPEN with asyncio.Lock
When a prefix breaker is in HALF_OPEN state, the system SHALL allow exactly one concurrent request to pass through as a probe. All other concurrent requests for the same prefix SHALL skip to fallback immediately. The probe request SHALL acquire an `asyncio.Lock` before executing.

#### Scenario: Single probe passes through
- **WHEN** breaker is HALF_OPEN and a request arrives
- **THEN** the lock is acquired, the request calls the LLM, and the breaker records the result

#### Scenario: Concurrent requests skip to fallback
- **WHEN** breaker is HALF_OPEN and a probe is already in progress
- **THEN** other requests for the same prefix skip to fallback without waiting

### Requirement: Fallback chain filtering
When the fallback chain is traversed (`_try_fallback` / `_try_fallback_stream`), the system SHALL skip any model whose prefix breaker is in OPEN state. Models whose prefix breaker is CLOSED or HALF_OPEN SHALL be attempted normally.

#### Scenario: Skip OPEN prefix in fallback
- **WHEN** fallback chain is `["openai/gpt-4o-mini", "anthropic/claude-3.5"]` and `"openai"` breaker is OPEN
- **THEN** `"openai/gpt-4o-mini"` is skipped and `"anthropic/claude-3.5"` is attempted

#### Scenario: All prefixes OPEN
- **WHEN** all fallback models have OPEN breakers
- **THEN** `RuntimeError("All models failed")` is raised

#### Scenario: CLOSED prefix in fallback
- **WHEN** fallback model has a CLOSED breaker
- **THEN** the model is attempted normally

### Requirement: LLMService integration
`LLMService` SHALL accept an optional `CircuitBreakerManager` in its constructor. When present, `chat()` and `chat_stream()` SHALL check breaker state before calling LiteLLM. If the primary model's prefix breaker is OPEN, the call SHALL skip directly to fallback. After each LiteLLM call (success or failure), the breaker SHALL record the result.

#### Scenario: Primary model prefix is OPEN
- **WHEN** `chat()` is called with model `"openai/gpt-4o"` and `"openai"` breaker is OPEN
- **THEN** LiteLLM is not called; fallback chain is traversed immediately

#### Scenario: Successful call records success
- **WHEN** LiteLLM call succeeds for model `"openai/gpt-4o"`
- **THEN** `record_success("openai/gpt-4o")` is called on the breaker

#### Scenario: Failed call records failure
- **WHEN** LiteLLM call fails for model `"openai/gpt-4o"`
- **THEN** `record_failure("openai/gpt-4o")` is called on the breaker

#### Scenario: No breaker configured
- **WHEN** `LLMService` is constructed without a `CircuitBreakerManager`
- **THEN** behavior is identical to the current implementation (no breaker checks)

### Requirement: State-change callback hook
`CircuitBreakerManager` SHALL accept an optional `on_state_change` callback of type `Callable[[str, CircuitState, CircuitState], None]`. When any prefix breaker transitions state, the callback SHALL be invoked with `(prefix, old_state, new_state)`. If no callback is provided, state transitions occur silently.

#### Scenario: Callback invoked on state change
- **WHEN** `"openai"` breaker transitions from CLOSED to OPEN
- **THEN** `on_state_change("openai", CircuitState.CLOSED, CircuitState.OPEN)` is called

#### Scenario: No callback configured
- **WHEN** `on_state_change` is `None` and a breaker transitions
- **THEN** no callback is invoked; breaker transitions normally

### Requirement: Thread safety
`CircuitBreakerManager` SHALL be safe for concurrent use in an async context. Breaker creation for new prefixes SHALL be protected against race conditions (two concurrent requests creating duplicate breakers for the same prefix).

#### Scenario: Concurrent requests for new prefix
- **WHEN** two requests arrive simultaneously for a prefix with no existing breaker
- **THEN** exactly one breaker instance is created for that prefix
