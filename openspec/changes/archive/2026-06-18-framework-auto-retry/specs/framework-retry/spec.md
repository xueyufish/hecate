## ADDED Requirements — 新增需求

### Requirement: RetryStrategy abstract base class — RetryStrategy 抽象基类

引擎 SHALL 在 `engine/retry.py` 中定义 `RetryStrategy` 作为抽象基类，包含两个抽象方法：`should_retry(error: Exception, attempt: int) -> bool` 和 `get_backoff(attempt: int) -> float`。此 ABC 遵循与现有引擎 ABC（SchedulerStrategy、EvictionPolicy）相同的模式。

#### Scenario: RetryStrategy is not instantiable — RetryStrategy 不可实例化

- **WHEN** 代码尝试直接实例化 `RetryStrategy()`
- **THEN** SHALL 抛出 `TypeError`（抽象方法未实现）

#### Scenario: Custom RetryStrategy implementation — 自定义 RetryStrategy 实现

- **WHEN** 子类同时实现了 `should_retry` 和 `get_backoff`
- **THEN** 该子类 SHALL 可实例化并可供 RetryExecutor 使用

### Requirement: NoRetryStrategy default implementation — NoRetryStrategy 默认实现

引擎 SHALL 提供 `NoRetryStrategy(RetryStrategy)` 作为默认实现。`should_retry()` SHALL 始终返回 `False`。`get_backoff()` SHALL 始终返回 `0.0`。

#### Scenario: NoRetryStrategy never retries — NoRetryStrategy 从不重试

- **WHEN** 调用 `NoRetryStrategy().should_retry(any_error, any_attempt)`
- **THEN** 它 SHALL 返回 `False`

#### Scenario: NoRetryStrategy zero backoff — NoRetryStrategy 零退避

- **WHEN** 调用 `NoRetryStrategy().get_backoff(any_attempt)`
- **THEN** 它 SHALL 返回 `0.0`

### Requirement: RetryExecutor non-streaming retry — RetryExecutor 非流式重试

引擎 SHALL 在 `engine/retry.py` 中提供 `RetryExecutor`，用于包装异步可调用对象的重试逻辑。对于非流式执行，RetryExecutor SHALL 调用函数，检查返回的 `WorkerResult.error`，如果根据策略错误可重试，则休眠 `get_backoff(attempt)` 秒并重试，最多达到策略的最大尝试次数。

#### Scenario: Successful execution on first attempt — 首次尝试执行成功

- **WHEN** RetryExecutor.execute() 被调用，且函数返回 WorkerResult(error=None)
- **THEN** 结果 SHALL 立即返回，不进行重试

#### Scenario: Retryable error then success — 可重试错误然后成功

- **WHEN** 函数在第 0 次尝试返回 WorkerResult(error=RateLimitError)，然后在第 1 次尝试返回 WorkerResult(error=None)
- **AND** 策略的 should_retry() 对第 0 次尝试返回 True
- **THEN** RetryExecutor SHALL 休眠 get_backoff(0) 秒，重试，并返回成功结果

#### Scenario: Non-retryable error propagates immediately — 不可重试的错误立即传播

- **WHEN** 函数返回 WorkerResult(error=AuthenticationError)
- **AND** 策略的 should_retry() 返回 False
- **THEN** RetryExecutor SHALL 立即返回失败的 WorkerResult，不进行重试

#### Scenario: Max attempts exhausted — 最大尝试次数耗尽

- **WHEN** 函数始终返回 WorkerResult(error=RateLimitError)
- **AND** 策略的 should_retry() 对第 0..N-1 次尝试返回 True，但最大尝试次数为 N
- **THEN** RetryExecutor SHALL 总共进行 N+1 次尝试（0..N）并返回最后一次失败的 WorkerResult

### Requirement: RetryExecutor stream-safe retry — RetryExecutor 流安全重试

对于流式执行，RetryExecutor SHALL 仅在没有任何 token 产出给调用方时重试。一旦第一个 token（非 WorkerResult 项）被产出，RetryExecutor SHALL 禁用重试并立即传播任何后续错误。

#### Scenario: Stream fails before first token — retryable — 流在第一个 token 前失败——可重试

- **WHEN** execute_stream() 在产出任何项之前抛出异常
- **AND** 策略的 should_retry() 返回 True
- **THEN** RetryExecutor SHALL 在退避延迟后重试流

#### Scenario: Stream fails after first token — no retry — 流在第一个 token 后失败——不重试

- **WHEN** execute_stream() 产出了一个或多个 token 字典，然后抛出异常
- **THEN** RetryExecutor SHALL 立即传播异常，不进行重试
- **AND** SHALL 不产出重复的 token

#### Scenario: Stream succeeds after retry — 流在重试后成功

- **WHEN** 第一次流尝试在第一个 token 前失败，且重试尝试成功
- **THEN** RetryExecutor SHALL 正常产出重试尝试的所有 token

### Requirement: Per-node retry config override — 每节点重试配置覆盖

PregelRuntime SHALL 支持通过 `node_config["retry"]` 字典进行每节点重试配置。当存在时，每节点配置 SHALL 与全局默认配置合并（每节点值覆盖相同键的全局值）。

#### Scenario: Global default used when no per-node config — 无每节点配置时使用全局默认

- **WHEN** 节点的配置不包含 "retry" 键
- **THEN** 该节点 SHALL 使用全局默认的 RetryStrategy

#### Scenario: Per-node override — 每节点覆盖

- **WHEN** 全局默认的 max_attempts=3
- **AND** node_config["retry"] = {"max_attempts": 5}
- **THEN** 该节点 SHALL 使用 max_attempts=5，所有其他设置继承自全局默认

### Requirement: EventStore retry observability — EventStore 重试可观测性

RetryExecutor SHALL 在每次重试尝试时向 EventStore（如果可用）发送一个 CUSTOM 事件。事件负载 SHALL 包含：event_name="RETRY"、node_id、尝试次数、error_type、error_message 和 backoff_seconds。

#### Scenario: Retry event emitted — 发出重试事件

- **WHEN** RetryExecutor 重试失败的节点执行
- **AND** 通过 execution_context 有可用的 EventStore
- **THEN** SHALL 追加一个 event_name="RETRY" 的 CUSTOM 事件，包含重试详细信息

#### Scenario: No event when EventStore unavailable — EventStore 不可用时无事件

- **WHEN** RetryExecutor 重试但没有可用的 EventStore
- **THEN** SHALL 不发出事件，重试正常进行

### Requirement: PregelRuntime integration — PregelRuntime 集成

PregelRuntime SHALL 接受一个可选的 `retry_strategy: RetryStrategy | None` 构造函数参数。当为 None 时，SHALL 使用 `NoRetryStrategy()` 作为默认值（向后兼容）。当提供时，RetryExecutor SHALL 通过策略包装 Worker 分发。

#### Scenario: Default behavior unchanged — 默认行为不变

- **WHEN** PregelRuntime 在没有 retry_strategy 参数的情况下构造
- **THEN** SHALL 使用 NoRetryStrategy，行为与变更前相同

#### Scenario: RetryStrategy enabled — 启用 RetryStrategy

- **WHEN** PregelRuntime 使用 DefaultRetryStrategy 构造
- **AND** Worker 返回可重试的错误
- **THEN** PregelRuntime SHALL 通过 RetryExecutor 重试 Worker，而不是立即抛出

### Requirement: DefaultRetryStrategy implementation in services layer — 服务层的 DefaultRetryStrategy 实现

服务层 SHALL 在 `services/validation/retry_policy.py` 中提供 `DefaultRetryStrategy(RetryStrategy)`。此实现 SHALL 使用 `ErrorClassifier.is_retryable_exception()` 进行重试决策，并使用带抖动的指数退避进行延迟。可配置参数：max_attempts、base_delay、max_delay、multiplier。

#### Scenario: Rate limit error is retryable — 速率限制错误可重试

- **WHEN** 调用 DefaultRetryStrategy.should_retry(openai.RateLimitError(...), attempt=0)
- **AND** max_attempts >= 1
- **THEN** 它 SHALL 返回 True

#### Scenario: Authentication error is not retryable — 认证错误不可重试

- **WHEN** 调用 DefaultRetryStrategy.should_retry(openai.AuthenticationError(...), attempt=0)
- **THEN** 它 SHALL 返回 False

#### Scenario: Exponential backoff with jitter — 带抖动的指数退避

- **WHEN** 调用 DefaultRetryStrategy.get_backoff(attempt=2)
- **AND** base_delay=1.0, multiplier=2.0, max_delay=30.0
- **THEN** 返回的延迟 SHALL 在 min(1.0 * 2^2, 30.0) = 4.0 秒的 50% 到 150% 之间
- **AND** 延迟 SHALL 在范围 [2.0, 6.0] 内
