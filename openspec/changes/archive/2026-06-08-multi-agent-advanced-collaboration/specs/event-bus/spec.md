## 新增的需求

### 需求：EventBus ABC 定义发布/订阅接口
引擎应在 `engine/eventbus.py` 中定义 `EventBus` ABC，包含抽象方法：`publish`、`subscribe`、`unsubscribe` 和 `close`。

#### 场景：发布事件
- **当** 调用 `publish(topic="agent_researcher", event=CollaborationEvent(...))`
- **则** 事件应异步投递给 `"agent_researcher"` 主题的所有订阅者

#### 场景：订阅主题
- **当** 使用异步可调用对象调用 `subscribe(topic="agent_researcher", handler=my_handler)`
- **则** `my_handler` 应为发布到 `"agent_researcher"` 的每个事件被调用，直到取消订阅

#### 场景：取消订阅主题
- **当** 调用 `unsubscribe(topic="agent_researcher", handler=my_handler)`
- **则** `my_handler` 不应再为该主题的后续事件被调用

#### 场景：关闭总线
- **当** 调用 `close()`
- **则** 所有待处理事件应刷新给订阅者，内部资源应被释放

### 需求：用于智能体协调的 CollaborationEvent 数据类
引擎应定义一个冻结的 `CollaborationEvent` 数据类，包含字段：`id`（UUID，自动生成）、`topic`（str）、`sender`（str，节点 ID）、`event_type`（CollaborationEventType 枚举）、`payload`（dict）、`timestamp`（datetime，自动生成）。

#### 场景：创建协作事件
- **当** 创建 `CollaborationEvent(topic="negotiation", sender="agent_a", event_type=CollaborationEventType.AGENT_MESSAGE, payload={"content": "I propose..."})`
- **则** 它应自动生成 `id`（UUID）和 `timestamp`（UTC 当前时间）

#### 场景：事件不可变性
- **当** 存在 CollaborationEvent 实例
- **则** 尝试设置任何字段应抛出 `FrozenInstanceError`

### 需求：用于智能体特定事件的 CollaborationEventType 枚举
引擎应定义一个字符串枚举 `CollaborationEventType`，包含值：`AGENT_MESSAGE`、`AGENT_REQUEST`、`AGENT_RESPONSE`、`TASK_ASSIGNED`、`TASK_COMPLETED`、`NEGOTIATION_PROPOSAL`、`NEGOTIATION_ACCEPT`、`NEGOTIATION_REJECT`、`DEBATE_ARGUMENT`、`DEBATE_REBUTTAL`、`DEBATE_CONCLUSION`。

#### 场景：使用标准协作事件类型
- **当** 引用 `CollaborationEventType.AGENT_MESSAGE`
- **则** 它应等于字符串 `"AGENT_MESSAGE"`

### 需求：InMemoryEventBus 提供会话范围发布/订阅
`InMemoryEventBus` 应使用每个主题的 `asyncio.Queue` 实现 EventBus，适用于会话范围的智能体协调。

#### 场景：发布和接收
- **当** 一个处理器订阅了主题 `"agent_a"`，然后调用 `publish("agent_a", event)`
- **则** 处理器应接收到该事件

#### 场景：多个订阅者
- **当** 3 个处理器订阅了同一主题且发布了一个事件
- **则** 所有 3 个处理器应接收到该事件

#### 场景：主题隔离
- **当** 一个处理器订阅了 `"agent_a"` 且一个事件发布到了 `"agent_b"`
- **则** 处理器不应接收到该事件

#### 场景：已取消订阅的处理器被忽略
- **当** 一个处理器从主题取消订阅后，一个事件被发布到该主题
- **则** 处理器不应接收到该事件

#### 场景：关闭刷新待处理事件
- **当** 5 个事件被发布到一个有订阅者的主题，然后调用 `close()`
- **则** 所有 5 个事件应在 close 返回前投递给订阅者

### 需求：PregelRuntime 接受可选的 EventBus
PregelRuntime 应接受一个可选的 `event_bus: EventBus | None = None` 参数。当提供时，运行时应通过 `execution_context` 将其传递给工作者。

#### 场景：execution_context 中的 EventBus
- **当** PregelRuntime 使用 `event_bus=InMemoryEventBus()` 创建
- **则** 传递给工作者的 `execution_context` 应包含 `{"event_bus": <EventBus 实例>}`

#### 场景：默认无 EventBus
- **当** PregelRuntime 在未提供 event_bus 的情况下创建
- **则** execution_context 不应包含 `"event_bus"` 键（或值应为 None）
