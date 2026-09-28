# channel-registry Specification

## Purpose

Defines pluggable channel behaviors: ACCUMULATOR reducer registration, the ChannelBehavior write-semantics contract with built-in implementations, channel-type registry mapping, and delegation from Channel, ChannelManager eviction checks, and ConflictResolver.

## Requirements
### Requirement: 可插拔 ACCUMULATOR reducer 注册

通道层 SHALL 提供具名 reducer 注册机制(`register_reducer(name, fn)`):`ChannelDef.reduce_fn` 引用注册名,写入时经对应 reducer 合并。内建注册 SHALL 至少覆盖既有 `"add"`(行为不变);`"append"`(向列表追加)SHALL 作为第二个内建 reducer 提供,覆盖 map-reduce 收集场景。MERGE 聚合写入 ACCUMULATOR 通道时 SHALL 经该通道的 reducer 合并。

#### Scenario: 自定义 reducer 参与合并
- **WHEN** 注册具名 reducer `merge_dicts` 并将某 ACCUMULATOR 通道的 `reduce_fn` 指向它
- **THEN** 并发分支对该通道的写入 SHALL 逐个经 `merge_dicts` 合并,SHALL NOT 互相覆盖

#### Scenario: append 内建 reducer 收集分支产出
- **WHEN** MERGE 将 5 个分支产出写入 `reduce_fn: "append"` 的 ACCUMULATOR 通道
- **THEN** 通道值 SHALL 为按写入序排列的 5 元素列表


### Requirement: ChannelBehavior ABC defines write semantics contract
The engine SHALL define a `ChannelBehavior` ABC in `runtime/channel.py` with 4 abstract methods: `initial_value(defn) -> Any`, `write(current, value, defn) -> Any`, `is_evictable() -> bool`, and `resolve_conflict(current, proposed) -> Any`.

#### Scenario: Custom behavior implementation
- **WHEN** a class extends ChannelBehavior and implements all 4 methods
- **THEN** it SHALL be usable as a registered channel type

#### Scenario: Missing abstract method
- **WHEN** a class extends ChannelBehavior but does not implement `write()`
- **THEN** instantiation SHALL raise TypeError

### Requirement: Built-in behaviors implement existing semantics
The engine SHALL provide 3 built-in ChannelBehavior implementations: `LastValueBehavior`, `TopicBehavior`, `AccumulatorBehavior`. `AccumulatorBehavior` SHALL resolve `ChannelDef.reduce_fn` through the reducer registry when a name is set: built-in `"add"` (numeric summation) and `"append"` (list append) are pre-registered, and any registered custom reducer applies its function. A non-None `reduce_fn` naming an unregistered reducer SHALL fail at channel registration / graph compile time with an explicit error naming the missing reducer — the previous silent overwrite fallback for unknown named reducers SHALL be removed. `reduce_fn=None` SHALL keep its existing overwrite semantics.

#### Scenario: LastValueBehavior write
- **WHEN** `LastValueBehavior.write("old", "new", defn)` is called
- **THEN** it SHALL return `"new"`

#### Scenario: LastValueBehavior initial value
- **WHEN** `LastValueBehavior.initial_value(defn)` is called with `defn.default=None`
- **THEN** it SHALL return `None`

#### Scenario: LastValueBehavior eviction
- **WHEN** `LastValueBehavior.is_evictable()` is called
- **THEN** it SHALL return `False`

#### Scenario: LastValueBehavior conflict
- **WHEN** `LastValueBehavior.resolve_conflict("old", "new")` is called
- **THEN** it SHALL return `"new"` (last-write-wins)

#### Scenario: TopicBehavior write scalar
- **WHEN** `TopicBehavior.write([1, 2], 3, defn)` is called
- **THEN** it SHALL return `[1, 2, 3]`

#### Scenario: TopicBehavior write list
- **WHEN** `TopicBehavior.write([1, 2], [3, 4], defn)` is called
- **THEN** it SHALL return `[1, 2, 3, 4]`

#### Scenario: TopicBehavior initial value
- **WHEN** `TopicBehavior.initial_value(defn)` is called
- **THEN** it SHALL return `[]`

#### Scenario: TopicBehavior eviction
- **WHEN** `TopicBehavior.is_evictable()` is called
- **THEN** it SHALL return `True`

#### Scenario: TopicBehavior conflict
- **WHEN** `TopicBehavior.resolve_conflict([1, 2], [2, 3])` is called
- **THEN** it SHALL return a merged list with deduplication `[1, 2, 3]`

#### Scenario: AccumulatorBehavior write
- **WHEN** `AccumulatorBehavior.write(5, 3, defn)` is called with `defn.reduce_fn="add"`
- **THEN** it SHALL return `8`

#### Scenario: AccumulatorBehavior write unknown reduce
- **WHEN** `AccumulatorBehavior.write(5, 3, defn)` is called with `defn.reduce_fn=None`
- **THEN** it SHALL return `3` (overwrite)

#### Scenario: AccumulatorBehavior unregistered named reducer fails registration
- **WHEN** a channel registers with `defn.reduce_fn="concat"` and no reducer named `"concat"` is registered
- **THEN** registration SHALL raise an explicit error naming the missing reducer instead of silently overwriting values

#### Scenario: AccumulatorBehavior registered custom reducer applies
- **WHEN** reducer `merge_dicts` is registered and `AccumulatorBehavior.write({"a": 1}, {"b": 2}, defn)` is called with `defn.reduce_fn="merge_dicts"`
- **THEN** it SHALL return `{"a": 1, "b": 2}`

#### Scenario: AccumulatorBehavior initial value
- **WHEN** `AccumulatorBehavior.initial_value(defn)` is called with `defn.initial=0`
- **THEN** it SHALL return `0`

#### Scenario: AccumulatorBehavior eviction
- **WHEN** `AccumulatorBehavior.is_evictable()` is called
- **THEN** it SHALL return `False`

#### Scenario: AccumulatorBehavior conflict
- **WHEN** `AccumulatorBehavior.resolve_conflict(5, 3)` is called
- **THEN** it SHALL return `8` (sum)


### Requirement: ChannelTypeRegistry maps names to behaviors
The engine SHALL provide a module-level registry with functions `register(name, behavior)`, `get(name) -> ChannelBehavior`, and `list_types() -> list[str]`. The registry SHALL pre-register "last_value", "topic", "persistent_topic", and "accumulator" at import time.

#### Scenario: Pre-registered types
- **WHEN** the engine module is imported
- **THEN** `list_types()` SHALL return at least `["last_value", "topic", "persistent_topic", "accumulator"]`

#### Scenario: Get registered type
- **WHEN** `get("topic")` is called
- **THEN** it SHALL return a `TopicBehavior` instance

#### Scenario: Get unknown type
- **WHEN** `get("unknown_type")` is called
- **THEN** it SHALL raise `KeyError`

#### Scenario: Register custom type
- **WHEN** `register("priority_queue", MyPriorityBehavior())` is called
- **THEN** `get("priority_queue")` SHALL return the registered behavior

#### Scenario: Persistent_topic maps to TopicBehavior
- **WHEN** `get("persistent_topic")` is called
- **THEN** it SHALL return a `TopicBehavior` instance (same as "topic")

### Requirement: Channel delegates to ChannelBehavior
`Channel.write()` SHALL look up the behavior from the registry and call `behavior.write(current, value, defn)` instead of using if/elif chains. `Channel._initial_value()` SHALL delegate to `behavior.initial_value(defn)`.

#### Scenario: Channel write delegates
- **WHEN** a TOPIC channel receives `write("hello")`
- **THEN** it SHALL call `TopicBehavior.write(current, "hello", defn)` and store the result

#### Scenario: Channel initial value delegates
- **WHEN** a channel is created with type ACCUMULATOR and initial=0
- **THEN** it SHALL call `AccumulatorBehavior.initial_value(defn)` to set the starting value

### Requirement: ChannelManager delegates eviction check to behavior
`ChannelManager.write()` SHALL check `behavior.is_evictable()` instead of comparing against `ChannelType.TOPIC | PERSISTENT_TOPIC`.

#### Scenario: Evictable channel
- **WHEN** a TOPIC channel exceeds eviction threshold
- **THEN** `ChannelManager.write()` SHALL apply eviction policy

#### Scenario: Non-evictable channel
- **WHEN** a LAST_VALUE channel is written to
- **THEN** `ChannelManager.write()` SHALL NOT check eviction policy

### Requirement: ConflictResolver delegates to ChannelBehavior
`ConflictResolver.resolve()` SHALL accept a `ChannelBehavior` parameter and delegate conflict resolution to `behavior.resolve_conflict()` instead of using string-based if/elif chains.

#### Scenario: Topic conflict resolution
- **WHEN** `resolve(channel_key, [1,2], [3,4], behavior=TopicBehavior())` is called
- **THEN** it SHALL return `ConflictResult(resolved=True, final_value=[1,2,3,4], strategy_used="merge_list")`

#### Scenario: Unknown behavior conflict fallback
- **WHEN** a custom behavior's `resolve_conflict()` raises an exception
- **THEN** `ConflictResolver` SHALL fall back to last-write-wins

