## ADDED Requirements — 新增需求

### Requirement：Event 数据类捕获细粒度执行状态 — Event 数据类捕获细粒度执行状态
引擎 SHALL 在 `engine/eventstore.py` 中定义一个不可变的 `Event` 数据类，包含字段：`id`（UUID，自动生成）、`session_id`（UUID）、`superstep`（int）、`event_type`（EventType 枚举）、`node_id`（str | None）、`timestamp`（datetime，自动生成）、`payload`（dict）。

#### Scenario：创建节点执行事件
- **WHEN** 使用 `session_id`、`superstep=3`、`event_type=NodeType.NODE_START`、`node_id="agent_1"` 创建一个 Event
- **THEN** 它 SHALL 具有自动生成的 `id`（UUID）、`timestamp`（UTC 当前时间）和默认值 `payload={}`

#### Scenario：事件不可变性
- **WHEN** 一个 Event 实例存在
- **THEN** 尝试设置任何字段 SHALL 引发 `FrozenInstanceError`

### Requirement: EventType 枚举定义标准事件类别 — EventType 枚举定义标准事件类别
引擎 SHALL 定义一个字符串枚举 `EventType`，值为：`NODE_START`、`NODE_END`、`TOOL_CALL`、`TOOL_RESULT`、`CHANNEL_WRITE`、`LLM_REQUEST`、`LLM_RESPONSE`、`INTERRUPT`、`RESUME`、`ERROR`、`CUSTOM`。

#### Scenario：使用标准事件类型
- **WHEN** 引用 `EventType.TOOL_CALL`
- **THEN** 它 SHALL 等于字符串 `"TOOL_CALL"`

#### Scenario：自定义事件类型
- **WHEN** 使用 `event_type=EventType.CUSTOM` 和 `payload={"custom_type": "my_event"}` 创建一个事件
- **THEN** 该事件 SHALL 有效且可存储

### Requirement：EventStore ABC 定义仅追加的事件持久化 — EventStore ABC 定义仅追加的事件持久化
引擎 SHALL 定义一个 `EventStore` ABC，包含抽象方法：`append`、`get_events`、`replay`、`get_version`。

#### Scenario：追加事件
- **WHEN** 使用有效 Event 调用 `append(event)`
- **THEN** 它 SHALL 持久化该事件并返回其 UUID

#### Scenario：按会话查询事件
- **WHEN** 使用 `session_id` 和 `from_version=0` 调用 `get_events(session_id, from_version=0)`
- **THEN** 它 SHALL 返回该会话的所有事件

#### Scenario：增量查询事件
- **WHEN** 使用 `from_version=5` 调用 `get_events(session_id, from_version=5)`
- **THEN** 它 SHALL 仅返回版本号 > 5 的事件

#### Scenario：重放事件
- **WHEN** 使用 `session_id` 和 `from_version=0` 调用 `replay(session_id, from_version=0)`
- **THEN** 它 SHALL 通过 AsyncGenerator 产出事件

#### Scenario：获取版本号
- **WHEN** 为已有 3 个事件的会话调用 `get_version(session_id)`
- **THEN** 它 SHALL 返回 `3`

#### Scenario：无事件的会话版本号
- **WHEN** 为没有事件的会话调用 `get_version(session_id)`
- **THEN** 它 SHALL 返回 `0`

### Requirement：InMemoryEventStore 提供测试实现 — InMemoryEventStore 提供测试实现
一个 `InMemoryEventStore` SHALL 使用内存中的字典实现 EventStore，适用于测试。

#### Scenario：顺序版本号分配
- **WHEN** 为同一会话追加 3 个事件
- **THEN** 事件 SHALL 获得版本号 1、2、3（按顺序）

#### Scenario：空会话
- **WHEN** 为没有事件的会话调用 `get_events(session_id)`
- **THEN** 它 SHALL 返回空列表

#### Scenario：重放空会话
- **WHEN** 为没有事件的会话调用 `replay(session_id)`
- **THEN** 生成器 SHALL 不产出任何事件