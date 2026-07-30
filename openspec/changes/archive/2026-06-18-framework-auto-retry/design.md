## Context — 背景

功能 1.3.5g 交付了 `ErrorClassifier`（基于 isinstance 的异常分类，包含 `classify()` 和 `is_retryable_exception()`），而 `RetryPolicy`（指数退避 + 抖动 + 熔断器）自 P1 以来就已存在。然而，两者都没有接入 Pregel 执行引擎。当 Worker 返回 `WorkerResult(error=...)` 时，PregelRuntime 立即传播它（`raise result.error`，第 283 行）——没有任何重试尝试。

这意味着瞬时故障（LLM 速率限制、网络超时、工具执行超时）会中止整个图形执行。处理这些问题的基础设施已经存在，但被断开了连接。

执行引擎遵循一致的 ABC 模式：11 个抽象基类（SchedulerStrategy、EvictionPolicy、ContextEngine、GuardrailHooks 等）在 `engine/` 中定义了可插拔接口，实现位于引擎内部（InMemory*、NoOp*）或 `services/`（通过 EnginePort 适配器）。重试必须遵循相同的模式。

**分层约束**：`engine/` 没有外部导入（只有 jsonschema）。`services/` 可以从 `engine/` 导入。`ErrorClassifier` 位于 `services/validation/retry_policy.py` 并从 `engine/errors.py` 导入。因此，重试 ABC 必须位于 `engine/` 中，而使用 `ErrorClassifier` 的实现必须位于 `services/` 中。

## Goals / Non-Goals — 目标 / 非目标

**目标：**

- 将现有的 `ErrorClassifier` + `RetryPolicy` 退避逻辑接入 Pregel 执行引擎
- 新的 engine ABC（`RetryStrategy`）遵循现有的 11-ABC 模式
- RetryExecutor 组件，包装 `pool.dispatch()` 和 `worker.execute_stream()` 两条路径
- 流安全重试：只在第一个 token 产出前重试（Google ADK、Salesforce Agentforce、IBM watsonx 研究的行业共识）
- 通过 `node_config["retry"]` 支持每节点重试配置覆盖
- EventStore 可观测性：在每次重试尝试时发出 CUSTOM 事件
- 零破坏性变更：默认的 `NoRetryStrategy` 保持当前行为

**非目标：**

- 熔断器集成（保留在 LLM 服务层，1.3.5d/6.8c——正交关注点）
- Token 级流恢复（"从第 N 个 token 恢复"——未解决问题，没有平台这样做）
- 跨超步重试（重试是单超步内的每节点操作，不跨超步边界）
- 修改 Graph DSL JSON schema（重试配置是运行时 + node_config，不是图形定义）
- 提供商级重试（LiteLLM `num_retries` 已处理此问题）

## Decisions — 决策

### Decision 1: engine/retry.py 中的 RetryStrategy ABC

**选择**：在 `engine/retry.py` 中定义 `RetryStrategy` 抽象基类，以 `NoRetryStrategy` 为默认值。`DefaultRetryStrategy` 实现（使用 `ErrorClassifier`）位于 `services/validation/retry_policy.py`。

**理由**：Engine 层不能从 `services/` 导入。ErrorClassifier 在 services/ 中。通过在 engine/ 中定义 ABC 并在 services/ 中实现，PregelRuntime 可以在没有分层违规的情况下针对 ABC 进行类型提示。这与 SchedulerStrategy/EvictionPolicy/ContextEngine 模式相同。

**考虑的替代方案**：
- *RetryExecutor 完全在 services/*：PregelRuntime 将需要 `Any` 类型提示，失去类型安全性。破坏了 ABC 模式。
- *将 ErrorClassifier 移到 engine/*：可能但破坏了验证模块的内聚性。ErrorClassifier 是服务层关注点。

### Decision 2: RetryExecutor 作为独立组件（不在 PregelRuntime 中内联）

**选择**：在 `engine/retry.py` 中创建 `RetryExecutor` 类，它接受一个 `RetryStrategy` 并用重试逻辑包装可调用对象的执行。PregelRuntime 通过 RetryExecutor 委托调度/流处理。

**理由**：单一职责——PregelRuntime 管理超步循环；RetryExecutor 管理重试。RetryExecutor 可独立测试（注入 mock 策略，验证重试次数和退避）。非流式和流式路径都通过它委托。

**考虑的替代方案**：
- *在 PregelRuntime 超步循环中内联重试*：使已经复杂的 execute() 方法膨胀。难以隔离测试。
- *包装 DirectWorkerPool 的 RetryingWorkerPool*：WorkerPool 是关于调度机制（线程/进程）的，而不是重试语义。也不覆盖流式路径（execute_stream 绕过池）。

### Decision 3: 流安全重试（仅前 token）

**选择**：流式调用只在第一个 token 产出给调用方之前重试。一旦任何 token 被转发，错误立即传播而不重试。

**理由**：10 平台研究（Google ADK、Salesforce Agentforce、IBM watsonx、Claude Code）显示普遍共识：
- Google ADK Go PR #732："流式调用只在第一个响应产出前重试，以防止重复的部分内容。"
- Salesforce Agentforce：使用 `Reset: true` 信号丢弃累积的内容——但这是客户端的重置，不是透明的重试。
- 没有平台实现"从第 N 个 token 恢复"——这是一个未解决的问题。

**实现**：RetryExecutor 在流式处理期间跟踪 `first_token_yielded` 标志。如果在任何 token 之前发生异常，重试。如果在之后，传播。

**考虑的替代方案**：
- *缓冲所有 token，成功后产出*：破坏了流式 UX。不可接受。
- *重置信号 + 丢弃*：复杂，客户端中断。推迟到未来的增强。

### Decision 4: 每节点配置合并

**选择**：通过 PregelRuntime 构造函数注入的全局默认 `RetryConfig`。通过 `node_config["retry"]` 字典进行每节点覆盖。合并：`{**global_config, **node_config.get("retry", {})}`。

**理由**：功能目录 1.3.5h 明确要求"支持每 Worker 覆盖"。实现很简单——在构建每节点策略时进行字典合并。不需要更改 Graph DSL schema（node_config 已经是一个自由格式的字典）。

### Decision 5: 通过 CUSTOM 事件实现 EventStore 可观测性

**选择**：每次重试尝试发送一个 EventStore CUSTOM 事件，负载为：`{"event_name": "RETRY", "node_id": ..., "attempt": N, "error_type": ..., "error_message": ..., "backoff_seconds": ...}`。

**理由**：所有被研究的平台（Google ADK、Salesforce Agentforce、IBM watsonx、Temporal）都会发出重试可观测性事件。EventStore 是现有的引擎可观测性机制。CUSTOM 事件不需要新的 EventType 枚举成员，使变更最小化。

## Risks / Trade-offs — 风险 / 权衡

- **[重试在中断期间放大负载]** → 缓解：max_attempts 限制重试次数。LLM 服务层的熔断器（1.3.5d）独立防止级联故障。这些是互补的，不是冲突的。

- **[每节点配置复杂性]** → 低风险：`node_config["retry"]` 是一个可选字典。缺少键 = 使用全局默认。简单的合并语义。

- **[第一个 token 后的流式错误不重试]** → 设计如此。替代方案（重置+丢弃）对 UX 更具破坏性。用户可以手动重试对话轮次。

- **[services/ 中的 DefaultRetryStrategy 创建了从 services/ 到 engine/ 的 ABC 导入]** → 这是正确的方向（services → engine）。没有违规。

- **[现有 RetryPolicy.execute() 不直接重用]** → RetryPolicy 从函数捕获异常；引擎 Worker 返回 `WorkerResult(error=...)` 而不是抛出。RetryExecutor 适应 WorkerResult 模式。RetryPolicy 的退避算法在 DefaultRetryStrategy.get_backoff() 中重用。
