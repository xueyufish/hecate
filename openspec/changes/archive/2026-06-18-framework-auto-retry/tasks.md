## 1. 引擎 ABC: RetryStrategy + NoRetryStrategy

- [x] 1.1 创建 `src/hecate/engine/retry.py`，包含 `RetryStrategy(ABC)`，定义抽象方法 `should_retry(error: Exception, attempt: int) -> bool` 和 `get_backoff(attempt: int) -> float`
- [x] 1.2 实现 `NoRetryStrategy(RetryStrategy)` — `should_retry()` 始终返回 False，`get_backoff()` 始终返回 0.0
- [x] 1.3 在模块、RetryStrategy 类、NoRetryStrategy 类及所有公共方法上提供完整的文档字符串
- [x] 1.4 顶部使用 `from __future__ import annotations`，所有方法都有类型注解

## 2. RetryExecutor 组件

- [x] 2.1 在 `engine/retry.py` 中实现 `RetryExecutor` 类，构造函数为 `(strategy: RetryStrategy, event_store: Any = None)`
- [x] 2.2 实现 `async execute(func, *args, **kwargs) -> WorkerResult` — 非流式重试循环：调用 func，检查 WorkerResult.error，按策略重试，休眠 get_backoff()，发送 EventStore 事件
- [x] 2.3 实现 `async execute_stream(func, *args, **kwargs) -> AsyncGenerator` — 流式重试循环，带 `first_token_yielded` 标志：仅在第一个非 WorkerResult 项产出前重试；之后立即传播错误
- [x] 2.4 EventStore 集成：每次重试时，当 event_store 可用时追加 CUSTOM 事件，负载为 `{"event_name": "RETRY", "node_id": ..., "attempt": N, "error_type": ..., "error_message": ..., "backoff_seconds": ...}`
- [x] 2.5 优雅处理最大尝试次数耗尽——返回最后一次失败的 WorkerResult（非流式）或抛出（第一个 token 后的流式）

## 3. PregelRuntime 集成

- [x] 3.1 向 PregelRuntime 添加 `retry_strategy: RetryStrategy | None = None` 构造函数参数
- [x] 3.2 当为 None 时默认使用 `NoRetryStrategy()`（向后兼容）
- [x] 3.3 在构造函数中使用注入的策略创建 `RetryExecutor` 实例
- [x] 3.4 包装非流式分发路径（约第 257-264 行）：通过 `retry_executor.execute()` 路由 `pool.dispatch()` 而不是直接调用
- [x] 3.5 包装流式路径（约第 248-255 行）：通过 `retry_executor.execute_stream()` 路由 `worker.execute_stream()` 而不是直接调用
- [x] 3.6 每节点配置合并：读取 `node_config.get("retry", {})`，与全局配置合并，在构建每节点策略时（如果 node_config 有重试覆盖，为该节点创建临时策略）
- [x] 3.7 将 execution_context（session_id、superstep、event_store）传递给 RetryExecutor 以实现 EventStore 可观测性

## 4. 服务层的 DefaultRetryStrategy

- [x] 4.1 将 `DefaultRetryStrategy(RetryStrategy)` 添加到 `services/validation/retry_policy.py`
- [x] 4.2 构造函数参数：`max_attempts=3, base_delay=1.0, max_delay=30.0, multiplier=2.0, error_classifier: ErrorClassifier | None = None`
- [x] 4.3 实现 `should_retry()`：检查 attempt < max_attempts AND error_classifier.is_retryable_exception(error)
- [x] 4.4 实现 `get_backoff()`：`min(base_delay * multiplier**attempt, max_delay) * (0.5 + random.random())` — 重用现有 RetryPolicy 的退避算法
- [x] 4.5 从 `hecate.engine.retry` 导入 RetryStrategy

## 5. 测试 — 引擎层

- [x] 5.1 测试 RetryStrategy 不可实例化（抽象方法）
- [x] 5.2 测试 NoRetryStrategy.should_retry() 始终返回 False
- [x] 5.3 测试 NoRetryStrategy.get_backoff() 始终返回 0.0
- [x] 5.4 测试 RetryExecutor.execute() — 首次尝试成功（无重试）
- [x] 5.5 测试 RetryExecutor.execute() — 可重试错误然后成功（1 次重试）
- [x] 5.6 测试 RetryExecutor.execute() — 不可重试的错误立即传播
- [x] 5.7 测试 RetryExecutor.execute() — 最大尝试次数耗尽（返回最后一次失败结果）
- [x] 5.8 测试 RetryExecutor.execute_stream() — 流在第一个 token 前失败 → 重试成功
- [x] 5.9 测试 RetryExecutor.execute_stream() — 流在第一个 token 后失败 → 不重试，错误传播
- [x] 5.10 测试 RetryExecutor.execute_stream() — 重试时无重复 token
- [x] 5.11 测试重试时发出 EventStore CUSTOM 事件
- [x] 5.12 测试当 EventStore 为 None 时不发事件

## 6. 测试 — 集成

- [x] 6.1 测试带默认 NoRetryStrategy 的 PregelRuntime — 行为不变（现有测试通过）
- [x] 6.2 测试带 DefaultRetryStrategy 的 PregelRuntime — 可重试的 Worker 错误触发重试
- [x] 6.3 测试 PregelRuntime 每节点配置覆盖 — node_config["retry"] 覆盖全局设置
- [x] 6.4 测试带重试的 PregelRuntime 流式 — token 前失败重试，token 后失败传播

## 7. 测试 — DefaultRetryStrategy

- [x] 7.1 测试 DefaultRetryStrategy.should_retry() 对可重试错误（mock RateLimitError）返回 True
- [x] 7.2 测试 DefaultRetryStrategy.should_retry() 对不可重试错误（mock AuthenticationError）返回 False
- [x] 7.3 测试 DefaultRetryStrategy.should_retry() 当 attempt >= max_attempts 时返回 False
- [x] 7.4 测试 DefaultRetryStrategy.get_backoff() 返回的值在基础计算的预期抖动范围 [50%, 150%] 内
- [x] 7.5 测试 DefaultRetryStrategy.get_backoff() 遵守 max_delay 上限

## 8. 文档

- [x] 8.1 更新 AGENTS.md：将 RetryStrategy 添加到引擎 ABC 清单表（第 12 个 ABC）
- [x] 8.2 更新 AGENTS.md：将 engine/retry.py 添加到关键文件表
- [x] 8.3 验证没有引擎层违规（retry.py 不得从 services/ 或 models/ 导入）
- [x] 8.4 运行 ruff check + ruff format --check + mypy + pytest — 全部通过
