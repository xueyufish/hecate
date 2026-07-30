## ADDED Requirements — 新增需求

### Requirement: ChannelBehavior ABC 定义写入语义契约 — ChannelBehavior ABC 定义写入语义契约
引擎 SHALL 在 `engine/channel.py` 中定义一个 `ChannelBehavior` ABC，包含 4 个抽象方法：`initial_value(defn) -> Any`、`write(current, value, defn) -> Any`、`is_evictable() -> bool` 和 `resolve_conflict(current, proposed) -> Any`。

#### Scenario: 自定义行为实现
- **WHEN** 一个类继承 ChannelBehavior 并实现所有 4 个方法
- **THEN** 它 SHALL 可用作已注册的 channel 类型

#### Scenario: 缺少抽象方法
- **WHEN** 一个类继承 ChannelBehavior 但未实现 `write()`
- **THEN** 实例化 SHALL 引发 TypeError

### Requirement: 内置行为实现现有语义 — 内置行为实现现有语义
引擎 SHALL 提供 3 个内置的 ChannelBehavior 实现：`LastValueBehavior`、`TopicBehavior`、`AccumulatorBehavior`。

#### Scenario: LastValueBehavior 写入
- **WHEN** 调用 `LastValueBehavior.write("old", "new", defn)`
- **THEN** 它 SHALL 返回 `"new"`

#### Scenario: LastValueBehavior 初始值
- **WHEN** 使用 `defn.default=None` 调用 `LastValueBehavior.initial_value(defn)`
- **THEN** 它 SHALL 返回 `None`

#### Scenario: LastValueBehavior 驱逐
- **WHEN** 调用 `LastValueBehavior.is_evictable()`
- **THEN** 它 SHALL 返回 `False`

#### Scenario: LastValueBehavior 冲突
- **WHEN** 调用 `LastValueBehavior.resolve_conflict("old", "new")`
- **THEN** 它 SHALL 返回 `"new"`（最后写入者胜出）

#### Scenario: TopicBehavior 写入标量
- **WHEN** 调用 `TopicBehavior.write([1, 2], 3, defn)`
- **THEN** 它 SHALL 返回 `[1, 2, 3]`

#### Scenario: TopicBehavior 写入列表
- **WHEN** 调用 `TopicBehavior.write([1, 2], [3, 4], defn)`
- **THEN** 它 SHALL 返回 `[1, 2, 3, 4]`

#### Scenario: TopicBehavior 初始值
- **WHEN** 调用 `TopicBehavior.initial_value(defn)`
- **THEN** 它 SHALL 返回 `[]`

#### Scenario: TopicBehavior 驱逐
- **WHEN** 调用 `TopicBehavior.is_evictable()`
- **THEN** 它 SHALL 返回 `True`

#### Scenario: TopicBehavior 冲突
- **WHEN** 调用 `TopicBehavior.resolve_conflict([1, 2], [2, 3])`
- **THEN** 它 SHALL 返回去重合并后的列表 `[1, 2, 3]`

#### Scenario: AccumulatorBehavior 写入
- **WHEN** 使用 `defn.reduce_fn="add"` 调用 `AccumulatorBehavior.write(5, 3, defn)`
- **THEN** 它 SHALL 返回 `8`

#### Scenario: AccumulatorBehavior 写入未知 reduce
- **WHEN** 使用 `defn.reduce_fn=None` 调用 `AccumulatorBehavior.write(5, 3, defn)`
- **THEN** 它 SHALL 返回 `3`（覆盖）

#### Scenario: AccumulatorBehavior 初始值
- **WHEN** 使用 `defn.initial=0` 调用 `AccumulatorBehavior.initial_value(defn)`
- **THEN** 它 SHALL 返回 `0`

#### Scenario: AccumulatorBehavior 驱逐
- **WHEN** 调用 `AccumulatorBehavior.is_evictable()`
- **THEN** 它 SHALL 返回 `False`

#### Scenario: AccumulatorBehavior 冲突
- **WHEN** 调用 `AccumulatorBehavior.resolve_conflict(5, 3)`
- **THEN** 它 SHALL 返回 `8`（求和）

### Requirement: ChannelTypeRegistry 将名称映射到行为
引擎 SHALL 提供一个模块级 registry，包含函数 `register(name, behavior)`、`get(name) -> ChannelBehavior` 和 `list_types() -> list[str]`。该 registry SHALL 在导入时预注册 "last_value"、"topic"、"persistent_topic" 和 "accumulator"。

#### Scenario: 预注册类型
- **WHEN** 引擎模块被导入
- **THEN** `list_types()` SHALL 至少返回 `["last_value", "topic", "persistent_topic", "accumulator"]`

#### Scenario: 获取注册类型
- **WHEN** 调用 `get("topic")`
- **THEN** 它 SHALL 返回一个 `TopicBehavior` 实例

#### Scenario: 获取未知类型
- **WHEN** 调用 `get("unknown_type")`
- **THEN** 它 SHALL 引发 `KeyError`

#### Scenario: 注册自定义类型
- **WHEN** 调用 `register("priority_queue", MyPriorityBehavior())`
- **THEN** `get("priority_queue")` SHALL 返回注册的行为

#### Scenario: Persistent_topic 映射到 TopicBehavior
- **WHEN** 调用 `get("persistent_topic")`
- **THEN** 它 SHALL 返回一个 `TopicBehavior` 实例（与 "topic" 相同）

### Requirement: Channel 委托给 ChannelBehavior
`Channel.write()` SHALL 从 registry 查找行为并调用 `behavior.write(current, value, defn)`，而不是使用 if/elif 链。`Channel._initial_value()` SHALL 委托给 `behavior.initial_value(defn)`。

#### Scenario: Channel 写入委托
- **WHEN** 一个 TOPIC channel 接收到 `write("hello")`
- **THEN** 它 SHALL 调用 `TopicBehavior.write(current, "hello", defn)` 并存储结果

#### Scenario: Channel 初始值委托
- **WHEN** 使用类型 ACCUMULATOR 和 initial=0 创建一个 channel
- **THEN** 它 SHALL 调用 `AccumulatorBehavior.initial_value(defn)` 来设置起始值

### Requirement: ChannelManager 将驱逐检查委托给行为
`ChannelManager.write()` SHALL 检查 `behavior.is_evictable()`，而不是比较 `ChannelType.TOPIC | PERSISTENT_TOPIC`。

#### Scenario: 可驱逐的 channel
- **WHEN** 一个 TOPIC channel 超过驱逐阈值
- **THEN** `ChannelManager.write()` SHALL 应用驱逐策略

#### Scenario: 不可驱逐的 channel
- **WHEN** 写入一个 LAST_VALUE channel
- **THEN** `ChannelManager.write()` SHALL 不检查驱逐策略

### Requirement: ConflictResolver 委托给 ChannelBehavior
`ConflictResolver.resolve()` SHALL 接受一个 `ChannelBehavior` 参数，并将冲突解决委托给 `behavior.resolve_conflict()`，而不是使用基于字符串的 if/elif 链。

#### Scenario: Topic 冲突解决
- **WHEN** 调用 `resolve(channel_key, [1,2], [3,4], behavior=TopicBehavior())`
- **THEN** 它 SHALL 返回 `ConflictResult(resolved=True, final_value=[1,2,3,4], strategy_used="merge_list")`

#### Scenario: 未知行为冲突回退
- **WHEN** 自定义行为的 `resolve_conflict()` 引发异常
- **THEN** `ConflictResolver` SHALL 回退到最后写入者胜出
