## ADDED Requirements

### Requirement: RetryStrategy abstract base class

The engine SHALL define `RetryStrategy` as an abstract base class in `engine/retry.py` with two abstract methods: `should_retry(error: Exception, attempt: int) -> bool` and `get_backoff(attempt: int) -> float`. This ABC follows the same pattern as existing engine ABCs (SchedulerStrategy, EvictionPolicy).

#### Scenario: RetryStrategy is not instantiable

- **WHEN** code attempts to instantiate `RetryStrategy()` directly
- **THEN** a `TypeError` SHALL be raised (abstract methods not implemented)

#### Scenario: Custom RetryStrategy implementation

- **WHEN** a subclass implements both `should_retry` and `get_backoff`
- **THEN** the subclass SHALL be instantiable and usable by RetryExecutor

### Requirement: NoRetryStrategy default implementation

The engine SHALL provide `NoRetryStrategy(RetryStrategy)` as the default implementation. `should_retry()` SHALL always return `False`. `get_backoff()` SHALL always return `0.0`.

#### Scenario: NoRetryStrategy never retries

- **WHEN** `NoRetryStrategy().should_retry(any_error, any_attempt)` is called
- **THEN** it SHALL return `False`

#### Scenario: NoRetryStrategy zero backoff

- **WHEN** `NoRetryStrategy().get_backoff(any_attempt)` is called
- **THEN** it SHALL return `0.0`

### Requirement: RetryExecutor non-streaming retry

The engine SHALL provide `RetryExecutor` in `engine/retry.py` that wraps async callables with retry logic. For non-streaming execution, RetryExecutor SHALL call the function, check the returned `WorkerResult.error`, and if the error is retryable per the strategy, sleep for `get_backoff(attempt)` seconds and retry up to the strategy's max attempts.

#### Scenario: Successful execution on first attempt

- **WHEN** RetryExecutor.execute() is called with a function that returns WorkerResult(error=None)
- **THEN** the result SHALL be returned immediately with no retry attempts

#### Scenario: Retryable error then success

- **WHEN** the function returns WorkerResult(error=RateLimitError) on attempt 0, then WorkerResult(error=None) on attempt 1
- **AND** the strategy's should_retry() returns True for attempt 0
- **THEN** RetryExecutor SHALL sleep for get_backoff(0) seconds, retry, and return the successful result

#### Scenario: Non-retryable error propagates immediately

- **WHEN** the function returns WorkerResult(error=AuthenticationError)
- **AND** the strategy's should_retry() returns False
- **THEN** RetryExecutor SHALL return the failed WorkerResult immediately without retrying

#### Scenario: Max attempts exhausted

- **WHEN** the function always returns WorkerResult(error=RateLimitError)
- **AND** the strategy's should_retry() returns True for attempts 0..N-1 but the max attempts is N
- **THEN** RetryExecutor SHALL make N+1 total attempts (0..N) and return the last failed WorkerResult

### Requirement: RetryExecutor stream-safe retry

For streaming execution, RetryExecutor SHALL retry only if no tokens have been yielded to the caller. Once the first token (non-WorkerResult item) is yielded, RetryExecutor SHALL disable retry and propagate any subsequent error immediately.

#### Scenario: Stream fails before first token — retryable

- **WHEN** execute_stream() raises an exception before yielding any item
- **AND** the strategy's should_retry() returns True
- **THEN** RetryExecutor SHALL retry the stream after backoff delay

#### Scenario: Stream fails after first token — no retry

- **WHEN** execute_stream() yields one or more token dicts, then raises an exception
- **THEN** RetryExecutor SHALL propagate the exception immediately without retrying
- **AND** no duplicate tokens SHALL be yielded

#### Scenario: Stream succeeds after retry

- **WHEN** the first stream attempt fails before first token, and the retry attempt succeeds
- **THEN** RetryExecutor SHALL yield all tokens from the retry attempt normally

### Requirement: Per-node retry config override

PregelRuntime SHALL support per-node retry configuration via `node_config["retry"]` dict. When present, the per-node config SHALL be merged with the global default config (per-node values override global values for the same keys).

#### Scenario: Global default used when no per-node config

- **WHEN** a node's config does not contain a "retry" key
- **THEN** the global default RetryStrategy SHALL be used for that node

#### Scenario: Per-node override

- **WHEN** global default has max_attempts=3
- **AND** node_config["retry"] = {"max_attempts": 5}
- **THEN** that node SHALL use max_attempts=5, with all other settings inherited from the global default

### Requirement: EventStore retry observability

RetryExecutor SHALL emit a CUSTOM event to the EventStore (when available) on each retry attempt. The event payload SHALL include: event_name="RETRY", node_id, attempt number, error_type, error_message, and backoff_seconds.

#### Scenario: Retry event emitted

- **WHEN** RetryExecutor retries a failed node execution
- **AND** an EventStore is available via execution_context
- **THEN** a CUSTOM event SHALL be appended with event_name="RETRY" and the retry details

#### Scenario: No event when EventStore unavailable

- **WHEN** RetryExecutor retries but no EventStore is available
- **THEN** no event SHALL be emitted and retry SHALL proceed normally

### Requirement: PregelRuntime integration

PregelRuntime SHALL accept an optional `retry_strategy: RetryStrategy | None` constructor parameter. When None, `NoRetryStrategy()` SHALL be used as default (backward compatible). When provided, RetryExecutor SHALL wrap worker dispatch through the strategy.

#### Scenario: Default behavior unchanged

- **WHEN** PregelRuntime is constructed without retry_strategy parameter
- **THEN** NoRetryStrategy SHALL be used and behavior SHALL be identical to pre-change

#### Scenario: RetryStrategy enabled

- **WHEN** PregelRuntime is constructed with a DefaultRetryStrategy
- **AND** a worker returns a retryable error
- **THEN** PregelRuntime SHALL retry the worker via RetryExecutor instead of immediately raising

### Requirement: DefaultRetryStrategy implementation in services layer

The services layer SHALL provide `DefaultRetryStrategy(RetryStrategy)` in `services/validation/retry_policy.py`. This implementation SHALL use `ErrorClassifier.is_retryable_exception()` for retry decisions and exponential backoff with jitter for delays. Configurable parameters: max_attempts, base_delay, max_delay, multiplier.

#### Scenario: Rate limit error is retryable

- **WHEN** DefaultRetryStrategy.should_retry(openai.RateLimitError(...), attempt=0) is called
- **AND** max_attempts >= 1
- **THEN** it SHALL return True

#### Scenario: Authentication error is not retryable

- **WHEN** DefaultRetryStrategy.should_retry(openai.AuthenticationError(...), attempt=0) is called
- **THEN** it SHALL return False

#### Scenario: Exponential backoff with jitter

- **WHEN** DefaultRetryStrategy.get_backoff(attempt=2) is called
- **AND** base_delay=1.0, multiplier=2.0, max_delay=30.0
- **THEN** the returned delay SHALL be between 50% and 150% of min(1.0 * 2^2, 30.0) = 4.0 seconds
- **AND** the delay SHALL be in range [2.0, 6.0]
