## ADDED Requirements — 新增需求

### Requirement: StreamMode.MESSAGES yield 单个令牌 — StreamMode.MESSAGES yields individual tokens
当 `StreamMode.MESSAGES` 激活时，PregelRuntime 应在 yield 标准超步事件之前，在调度阶段从 Workers yield 单个令牌事件。

#### Scenario: 来自 LLM Worker 的令牌流式 — Token streaming from LLM Worker
- **当** 一个 CONVERSATION 节点以 StreamMode.MESSAGES 被调度
- **并且** Worker 在执行期间 yield 令牌
- **则** PregelRuntime 先为每个令牌 yield `{"type": "message", "content": "<token>"}`，然后在超步完成后 yield `{"type": "values", "state": <snapshot>}`

#### Scenario: 单节点多个令牌 — Multiple tokens from single node
- **当** Worker yield 了 50 个令牌后跟一个最终的 WorkerResult
- **则** PregelRuntime 应 yield 50 个 `{"type": "message"}` 事件，然后 yield 一个 `{"type": "values"}` 事件

#### Scenario: 非流式 Workers — Non-streaming Workers
- **当** Worker（如 ConditionWorker）不 yield 令牌
- **则** PregelRuntime 应在超步后仅 yield 标准的 `{"type": "values"}` 事件

### Requirement: Worker 流式接口 — Worker streaming interface
Workers 应支持可选的流式接口，在返回最终 WorkerResult 之前 yield 部分结果。

#### Scenario: Worker yield 令牌 — Worker yields tokens
- **当** Worker 的 execute 方法是 AsyncGenerator 而非协程
- **则** PregelRuntime 应消费生成器，将每个中间项 yield 为 MESSAGES 事件，并将最终 yield 的项作为 WorkerResult
