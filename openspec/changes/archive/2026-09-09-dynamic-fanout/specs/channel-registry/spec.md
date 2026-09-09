## ADDED Requirements

### Requirement: 可插拔 ACCUMULATOR reducer 注册

通道层 SHALL 提供具名 reducer 注册机制(`register_reducer(name, fn)`):`ChannelDef.reduce_fn` 引用注册名,写入时经对应 reducer 合并。内建注册 SHALL 至少覆盖既有 `"add"`(行为不变);`"append"`(向列表追加)SHALL 作为第二个内建 reducer 提供,覆盖 map-reduce 收集场景。MERGE 聚合写入 ACCUMULATOR 通道时 SHALL 经该通道的 reducer 合并。

#### Scenario: 自定义 reducer 参与合并
- **WHEN** 注册具名 reducer `merge_dicts` 并将某 ACCUMULATOR 通道的 `reduce_fn` 指向它
- **THEN** 并发分支对该通道的写入 SHALL 逐个经 `merge_dicts` 合并,SHALL NOT 互相覆盖

#### Scenario: append 内建 reducer 收集分支产出
- **WHEN** MERGE 将 5 个分支产出写入 `reduce_fn: "append"` 的 ACCUMULATOR 通道
- **THEN** 通道值 SHALL 为按写入序排列的 5 元素列表

## MODIFIED Requirements

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
