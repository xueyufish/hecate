## Context

Feature 1.3.5g shipped `ErrorClassifier` (isinstance-based exception classification with `classify()` and `is_retryable_exception()`) and `RetryPolicy` (exponential backoff + jitter + circuit breaker) has existed since P1. However, neither is wired into the Pregel execution engine. When a worker returns `WorkerResult(error=...)`, PregelRuntime immediately propagates it (`raise result.error`, line 283) — no retry attempt is made.

This means transient failures (LLM rate limits, network timeouts, tool execution timeouts) abort entire graph executions. The infrastructure to handle these exists but is disconnected.

The execution engine follows a consistent ABC pattern: 11 abstract base classes (SchedulerStrategy, EvictionPolicy, ContextEngine, GuardrailHooks, etc.) define pluggable interfaces in `engine/`, with implementations provided either in-engine (InMemory*, NoOp*) or in `services/` (via EnginePort adapters). Retry must follow this same pattern.

**Layering constraint**: `engine/` has zero external imports (only jsonschema). `services/` can import from `engine/`. `ErrorClassifier` lives in `services/validation/retry_policy.py` and imports from `engine/errors.py`. Therefore the retry ABC must live in `engine/` and the implementation using `ErrorClassifier` must live in `services/`.

## Goals / Non-Goals

**Goals:**

- Wire existing `ErrorClassifier` + `RetryPolicy` backoff logic into the Pregel execution engine
- New engine ABC (`RetryStrategy`) following the existing 11-ABC pattern
- RetryExecutor component that wraps both `pool.dispatch()` and `worker.execute_stream()` paths
- Stream-safe retry: retry only before first token yielded (industry consensus from Google ADK, Salesforce Agentforce, IBM watsonx research)
- Per-node retry config override via `node_config["retry"]`
- EventStore observability: emit CUSTOM events on each retry attempt
- Zero breaking changes: default `NoRetryStrategy` preserves current behavior

**Non-Goals:**

- Circuit breaker integration (stays in LLM service layer, 1.3.5d/6.8c — orthogonal concern)
- Token-level stream resume ("resume from Nth token" — unsolved problem, no platform does this)
- Retry across supersteps (retry is per-node within a single superstep, not across superstep boundaries)
- Modifying the Graph DSL JSON schema (retry config is runtime + node_config, not graph definition)
- Provider-level retry (LiteLLM `num_retries` handles this already)

## Decisions

### Decision 1: RetryStrategy ABC in engine/retry.py

**Choice**: Define `RetryStrategy` abstract base class in `engine/retry.py` with `NoRetryStrategy` default. `DefaultRetryStrategy` implementation (using `ErrorClassifier`) lives in `services/validation/retry_policy.py`.

**Rationale**: Engine layer cannot import from `services/`. ErrorClassifier is in services/. By defining the ABC in engine/ and implementation in services/, PregelRuntime can type-hint against the ABC without layering violations. This is identical to the SchedulerStrategy/EvictionPolicy/ContextEngine pattern.

**Alternatives considered**:
- *RetryExecutor entirely in services/*: PregelRuntime would need `Any` type hint, losing type safety. Breaks the ABC pattern.
- *Move ErrorClassifier to engine/*: Possible but breaks the validation module's cohesion. ErrorClassifier is a services-layer concern.

### Decision 2: RetryExecutor as a separate component (not inline in PregelRuntime)

**Choice**: Create `RetryExecutor` class in `engine/retry.py` that takes a `RetryStrategy` and wraps callable execution with retry logic. PregelRuntime delegates dispatch/streaming through RetryExecutor.

**Rationale**: Single Responsibility — PregelRuntime manages the superstep loop; RetryExecutor manages retry. RetryExecutor is independently testable (inject mock strategy, verify retry count and backoff). Both non-streaming and streaming paths delegate through it.

**Alternatives considered**:
- *Inline retry in PregelRuntime superstep loop*: Bloats the already-complex execute() method. Hard to test in isolation.
- *RetryingWorkerPool wrapping DirectWorkerPool*: WorkerPool is about dispatch mechanism (thread/process), not retry semantics. Also doesn't cover streaming path (execute_stream bypasses pool).

### Decision 3: Stream-safe retry (pre-token only)

**Choice**: Streaming calls retry only before the first token is yielded to the caller. Once any token has been forwarded, errors propagate immediately without retry.

**Rationale**: 10-platform research (Google ADK, Salesforce Agentforce, IBM watsonx, Claude Code) shows universal consensus:
- Google ADK Go PR #732: "Streaming calls only retry before the first yielded response to prevent duplicate partial content."
- Salesforce Agentforce: Uses `Reset: true` signal to discard accumulated content — but this is a client-facing reset, not transparent retry.
- No platform implements "resume from Nth token" — it's an unsolved problem.

**Implementation**: RetryExecutor tracks `first_token_yielded` flag during streaming. If an exception occurs before any token, retry. If after, propagate.

**Alternatives considered**:
- *Buffer all tokens, yield after success*: Destroys streaming UX. Unacceptable.
- *Reset signal + discard*: Complex, client-facing disruption. Defer to future enhancement.

### Decision 4: Per-node config merge

**Choice**: Global default `RetryConfig` injected via PregelRuntime constructor. Per-node override via `node_config["retry"]` dict. Merge: `{**global_config, **node_config.get("retry", {})}`.

**Rationale**: Feature-catalog 1.3.5h explicitly requires "per-Worker override supported". Implementation is trivial — a dict merge when constructing the per-node strategy. No Graph DSL schema change needed (node_config is already a free-form dict).

### Decision 5: EventStore observability via CUSTOM events

**Choice**: Each retry attempt emits an EventStore CUSTOM event with payload: `{"event_name": "RETRY", "node_id": ..., "attempt": N, "error_type": ..., "error_message": ..., "backoff_seconds": ...}`.

**Rationale**: All researched platforms (Google ADK, Salesforce Agentforce, IBM watsonx, Temporal) emit retry observability events. EventStore is the existing engine observability mechanism. CUSTOM events require no new EventType enum member, keeping the change minimal.

## Risks / Trade-offs

- **[Retry amplifies load during outages]** → Mitigated: max_attempts caps retries. Circuit breaker in LLM service layer (1.3.5d) independently prevents cascading failures. These are complementary, not conflicting.

- **[Per-node config complexity]** → Low risk: `node_config["retry"]` is an optional dict. Absent key = use global default. Simple merge semantics.

- **[Streaming errors after first token are not retried]** → By design. Alternative (reset+discard) is more disruptive to UX. Users can manually retry the conversation turn.

- **[DefaultRetryStrategy in services/ creates import from services/ to engine/ for the ABC]** → This is the correct direction (services → engine). No violation.

- **[Existing RetryPolicy.execute() is not reused directly]** → RetryPolicy catches exceptions from a function; engine Workers return `WorkerResult(error=...)` instead of raising. RetryExecutor adapts to the WorkerResult pattern. RetryPolicy's backoff math is reused in DefaultRetryStrategy.get_backoff().
