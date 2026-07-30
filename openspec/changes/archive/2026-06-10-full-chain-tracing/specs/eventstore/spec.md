## MODIFIED Requirements — 修改的需求

### 需求：Event 数据类捕获细粒度执行状态
引擎应在 `engine/eventstore.py` 中定义一个不可变的 `Event` 数据类，字段包括：`id`（UUID，自动生成）、`session_id`（UUID）、`superstep`（int）、`event_type`（EventType 枚举）、`node_id`（str | None）、`timestamp`（datetime，自动生成）、`payload`（dict）、`trace_id`（str | None，默认 None）

#### 场景：创建带追踪关联的节点执行事件
- **当** 使用 `session_id`、`superstep=3`、`event_type=EventType.NODE_START`、`node_id="agent_1"`、`trace_id="abc123"` 创建 Event
- **则** 应具有自动生成的 `id`（UUID）、`timestamp`（UTC 当前时间）、默认 `payload={}` 和 `trace_id="abc123"`

#### 场景：无追踪上下文时创建事件
- **当** 未指定 `trace_id` 时创建 Event
- **则** `trace_id` 应默认为 `None`

#### 场景：事件不可变性
- **当** 存在 Event 实例
- **则** 尝试设置任何字段应抛出 `FrozenInstanceError`

### 需求：EventType 枚举定义标准事件类别
引擎应定义一个字符串枚举 `EventType`，值包括：`NODE_START`、`NODE_END`、`TOOL_CALL`、`TOOL_RESULT`、`CHANNEL_WRITE`、`LLM_REQUEST`、`LLM_RESPONSE`、`INTERRUPT`、`RESUME`、`ERROR`、`CUSTOM`

#### 场景：使用标准事件类型
- **当** 引用 `EventType.TOOL_CALL`
- **则** 应等于字符串 `"TOOL_CALL"`

#### 场景：自定义事件类型
- **当** 使用 `event_type=EventType.CUSTOM` 和 `payload={"custom_type": "my_event"}` 创建事件
- **则** 事件应有效且可存储

### 需求：EventStore ABC 定义仅追加的事件持久化
引擎应定义一个 `EventStore` ABC，带抽象方法：`append`、`get_events`、`replay`、`get_version`。`append` 方法应接受可能包含 `trace_id` 字段的 `Event`，用于将引擎事件与应用级追踪关联

#### 场景：追加事件
- **当** 使用有效 Event 调用 `append(event)`
- **则** 应使用自动分配的版本号持久化事件并返回事件的 UUID

#### 场景：携带 trace_id 追加事件
- **当** 使用 `trace_id="trace_abc"` 的 Event 调用 `append(event)`
- **则** 存储的事件应保留 `trace_id="trace_abc"` 以便后续关联查询
