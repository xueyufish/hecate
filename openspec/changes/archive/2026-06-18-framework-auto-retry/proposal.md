## Why — 为什么

功能 1.3.5g 交付了 `ErrorClassifier`，包含 `classify()` 和 `is_retryable_exception()`——但没有生产环境的调用者。`RetryPolicy` 包含指数退避、抖动和熔断器，自 P1 以来就已存在——但在执行引擎中也没有调用者。PregelRuntime 第 283 行立即传播 Worker 错误（`raise result.error`）而不进行重试，这意味着瞬时故障（速率限制、超时、网络波动）会不必要地中止整个图形执行。基础设施已经建立；需要将其接入。

## What Changes — 变更内容

- **新的 engine ABC**：`engine/retry.py` 中的 `RetryStrategy`——重试决策的抽象接口（`should_retry`、`get_backoff`），遵循现有的引擎 ABC 模式（SchedulerStrategy、EvictionPolicy 等）
- **新的 `RetryExecutor`**：引擎级组件，用重试逻辑包装 Worker 分发。处理非流式（`pool.dispatch()`）和流式（`worker.execute_stream()`）路径。
- **流安全重试**：流式调用只在第一个 token 产出前重试。发送 token 后，错误立即传播——防止 token 重复。（Google ADK、Salesforce Agentforce、IBM watsonx 研究的行业共识。）
- **每节点重试配置**：PregelRuntime 构造函数中的全局默认 `RetryConfig`；通过 `node_config["retry"]` 进行每节点覆盖。合并策略：`{**global_default, **node_config.get("retry", {})}`。
- **EventStore 可观测性**：每次重试尝试发送一个 CUSTOM 事件，包含 node_id、尝试次数、错误类型和退避延迟。
- **PregelRuntime 集成**：新的可选 `retry_strategy: RetryStrategy | None` 构造函数参数。默认值：`NoRetryStrategy()`（向后兼容）。

## Capabilities — 能力

### New Capabilities — 新增能力

- `framework-retry`：RetryStrategy ABC、RetryExecutor 组件、NoRetryStrategy 默认值和 DefaultRetryStrategy 实现（在 services/ 中使用来自 1.3.5g 的 ErrorClassifier）。涵盖非流式重试、流安全重试、每节点配置合并和 EventStore 可观测性。

### Modified Capabilities — 修改的能力

无。这是一个在现有引擎基础设施之上分层的新能力。

## Impact — 影响

- **新文件**：`src/hecate/engine/retry.py`（RetryStrategy ABC + RetryExecutor + NoRetryStrategy），测试文件
- **修改的文件**：`src/hecate/engine/pregel.py`（添加 retry_strategy 参数，使用 RetryExecutor），`src/hecate/services/validation/retry_policy.py`（添加 DefaultRetryStrategy）
- **依赖关系**：使用来自 1.3.5g 的 `ErrorClassifier.is_retryable_exception()`（已交付）
- **无破坏性变更**：默认的 `NoRetryStrategy` 保持当前行为。重试通过构造函数参数选择加入。
- **引擎层完整性**：RetryStrategy ABC 位于 `engine/retry.py`（零外部依赖）。DefaultRetryStrategy 实现位于 `services/`（可以导入 ErrorClassifier）。与 SchedulerStrategy/ContextEngine 模式一致。
