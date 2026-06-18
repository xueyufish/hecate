## Why

Feature 1.3.5g delivered `ErrorClassifier` with `classify()` and `is_retryable_exception()` — but zero production callers. `RetryPolicy` with exponential backoff, jitter, and circuit breaker has existed since P1 — but zero callers in the execution engine. PregelRuntime line 283 immediately propagates worker errors (`raise result.error`) with no retry, meaning transient failures (rate limits, timeouts, network blips) abort entire graph executions unnecessarily. The infrastructure is built; it needs to be wired in.

## What Changes

- **New engine ABC**: `RetryStrategy` in `engine/retry.py` — abstract interface for retry decisions (`should_retry`, `get_backoff`), following the existing engine ABC pattern (SchedulerStrategy, EvictionPolicy, etc.)
- **New `RetryExecutor`**: Engine-level component that wraps worker dispatch with retry logic. Handles both non-streaming (`pool.dispatch()`) and streaming (`worker.execute_stream()`) paths.
- **Stream-safe retry**: Streaming calls retry only before the first token is yielded. After tokens are sent, errors propagate immediately — preventing token duplication. (Industry consensus from Google ADK, Salesforce Agentforce, IBM watsonx research.)
- **Per-node retry config**: Global default `RetryConfig` in PregelRuntime constructor; per-node override via `node_config["retry"]`. Merge strategy: `{**global_default, **node_config.get("retry", {})}`.
- **EventStore observability**: Each retry attempt emits a CUSTOM event with node_id, attempt number, error type, and backoff delay.
- **PregelRuntime integration**: New optional `retry_strategy: RetryStrategy | None` constructor parameter. Default: `NoRetryStrategy()` (backward compatible).

## Capabilities

### New Capabilities

- `framework-retry`: RetryStrategy ABC, RetryExecutor component, NoRetryStrategy default, and DefaultRetryStrategy implementation (in services/ using ErrorClassifier from 1.3.5g). Covers non-streaming retry, stream-safe retry, per-node config merge, and EventStore observability.

### Modified Capabilities

_None._ This is a new capability layered on top of existing engine infrastructure.

## Impact

- **New files**: `src/hecate/engine/retry.py` (RetryStrategy ABC + RetryExecutor + NoRetryStrategy), tests
- **Modified files**: `src/hecate/engine/pregel.py` (add retry_strategy param, use RetryExecutor), `src/hecate/services/validation/retry_policy.py` (add DefaultRetryStrategy)
- **Dependencies**: Uses `ErrorClassifier.is_retryable_exception()` from 1.3.5g (already shipped)
- **No breaking changes**: Default `NoRetryStrategy` preserves current behavior. Retry is opt-in via constructor parameter.
- **Engine layer integrity**: RetryStrategy ABC lives in `engine/retry.py` (zero external deps). DefaultRetryStrategy implementation lives in `services/` (can import ErrorClassifier). Consistent with SchedulerStrategy/ContextEngine pattern.
