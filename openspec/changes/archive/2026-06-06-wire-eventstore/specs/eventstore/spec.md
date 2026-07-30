## MODIFIED Requirements — 修改的需求

### Requirement: PregelRuntime 接受可选的 EventStore — PregelRuntime 接受可选的 EventStore
PregelRuntime SHALL 在构造函数中接受可选的 `event_store: EventStore | None = None` 参数。当提供时，运行时 SHALL 在关键生命周期点记录执行事件。

#### Scenario: 默认无事件记录
- **WHEN** PregelRuntime 在没有 event_store 的情况下创建
- **THEN** 它 SHALL 在不记录事件的情况下执行（当前行为）

#### Scenario: 带 EventStore
- **WHEN** PregelRuntime 使用 `event_store=InMemoryEventStore()` 创建
- **THEN** 它 SHALL 在执行期间记录 NODE_START、NODE_END、CHANNEL_WRITE 和 SUPERSTEP_END 事件

#### Scenario: 会话开始事件
- **WHEN** PregelRuntime.execute() 以 initial_input 开始
- **THEN** 它 SHALL 记录一个 `payload.event_name="SESSION_START"` 的 CUSTOM 事件

#### Scenario: 恢复事件
- **WHEN** PregelRuntime.execute() 以 resume_value 开始
- **THEN** 它 SHALL 记录一个 RESUME 事件，载荷中包含被中断的节点 ID

#### Scenario: 中断事件
- **WHEN** 一个 worker 返回 Command(interrupt=...)
- **THEN** 它 SHALL 在保存 checkpoint 前记录一个 INTERRUPT 事件

#### Scenario: 错误事件
- **WHEN** 一个 worker 返回包含错误的结果
- **THEN** 它 SHALL 在抛出错误前记录一个 ERROR 事件

### Requirement: Worker 接受可选的 EventStore — Worker 接受可选的 EventStore
Worker ABC SHALL 在其构造函数中接受可选的 `event_store: EventStore | None = None` 参数。Worker.execute() SHALL 接受可选的 `execution_context: dict | None = None` 参数，包含 `session_id`、`superstep` 和 `event_store`。

#### Scenario: 默认无事件记录
- **WHEN** Worker 在没有 event_store 的情况下创建
- **THEN** 它 SHALL 在不记录事件的情况下执行（当前行为）

#### Scenario: PregelRuntime 传递执行上下文
- **WHEN** PregelRuntime 分发一个 worker
- **THEN** 它 SHALL 传递 `execution_context={"session_id": UUID, "superstep": int, "event_store": EventStore}`

#### Scenario: LLMWorker 记录 LLM 事件
- **WHEN** LLMWorker 使用 execution_context 中的 event_store 执行
- **THEN** 它 SHALL 在 LLM 调用前记录 LLM_REQUEST，在调用后记录 LLM_RESPONSE

#### Scenario: ToolWorker 记录工具事件
- **WHEN** ToolWorker 使用 execution_context 中的 event_store 执行
- **THEN** 它 SHALL 在工具调用前记录 TOOL_CALL，在调用后记录 TOOL_RESULT
