## MODIFIED Requirements

### Requirement: acquire_event_lock 锁接口（可选，默认 no-op）
EventStore SHALL 提供会话粒度的异步领取锁接口；接口签名保留 timeout_ms。默认实现是 no-op，但用于受保护写入的生产实现 SHALL 提供互斥，不能依赖单次 append 的锁代替查询与领取之间的互斥。InMemory 与 Postgres 实现 SHALL 对同一会话的查询与领取临界区互斥。Postgres 锁 SHALL 在正常、异常、取消退出后释放，不得残留在连接池连接上；等待 SHALL 遵守 timeout_ms，并向调用方传播失败。

#### Scenario: Exclusive claim
- **WHEN** 两个执行者领取同一会话的动作
- **THEN** 临界区互斥，后者读取前者已提交的领取

#### Scenario: Connection reused after exit
- **WHEN** 临界区正常、异常或取消退出后池连接被复用
- **THEN** 锁已释放，另一连接可领取相同会话

#### Scenario: Lock wait timeout
- **WHEN** 会话锁一直被其他连接持有，等待超过 timeout_ms
- **THEN** 获取失败并传播错误，不无限等待

#### Scenario: 默认实现是 no-op
- **WHEN** 只用于无互斥保证场景的 EventStore 默认 acquire_event_lock 被调用
- **THEN** 默认接口直接 yield；InMemory 与受保护写入的生产实现 SHALL 覆盖为互斥锁

#### Scenario: PostgresEventStore 继承 default 不覆盖
- **WHEN** 检查此前依赖 default no-op 的 PostgresEventStore 配置
- **THEN** 本轮更正为覆盖默认实现并取得会话粒度事务锁，不允许继续依赖单次 append 的锁代替领取互斥


### Requirement: PostgresEventStore persists EventStore events to PostgreSQL via ORM

services 层 SHALL 提供 `PostgresEventStore`（`src/hecate/studio/event_state/postgres_store.py`）实现 engine-layer `EventStore` ABC，把 `Event` 记录以 append-only 方式落表到 `events` 表。

实现 SHALL 复用既有 `async_session_factory`（来自 `hecate.core.database`），与 `PostgresSessionStateStore` 共享连接池与 PG 方言处理。

序列化 SHALL 把 `Event` 的 `payload` 字段存为 PostgreSQL `JSONB`（便于查询）；其它字段（`session_id`, `superstep`, `event_type`, `node_id`, `trace_id`, `version`, `id`）存为独立强类型列。整体 row 不 SHALL 使用 `pickle` 或自定义二进制格式。

`append` 方法 SHALL 在事务内为 event 赋值单调递增的 `version`——具体机制为独立的事务级 append advisory lock 串行化同 session 的版本分配，再查询 `MAX(version)`；该锁键空间 SHALL 与外层查询/领取锁隔离。`INSERT ... ON CONFLICT DO NOTHING` 保证 `(session_id, version)` 唯一性，冲突时（极罕见的 race）SHALL 重试或抛 `EventVersionConflictError`。

`get_events` SHALL 用 `SELECT ... WHERE session_id = ? AND version >= ? ORDER BY version ASC` 返回结果，反序列化为 `Event` 实例。

`get_version` SHALL 用 `SELECT MAX(version) FROM events WHERE session_id = ?`，无行时返回 `0`。

`replay` SHALL 复用 `get_events` 实现，按 version 升序 yield 每个 `Event`。

#### Scenario: append 后能按 version 升序读回

- **WHEN** 对同一 `session_id` 连续 `append` 3 个 Event，然后调 `get_events(session_id)`
- **THEN** 返回 list 长度为 3，versions 为 `[1, 2, 3]`，顺序与 append 一致

#### Scenario: get_events 支持版本过滤

- **WHEN** 10 个 Event 已 append，调用 `get_events(session_id, from_version=7)`
- **THEN** 返回 versions 为 `[7, 8, 9, 10]` 的 4 个 Event

#### Scenario: 同 session 并发 append 自然串行化

- **WHEN** 两个并发 `append` 同一 `session_id` 同时到达
- **THEN** PG append 事务锁让第二个等待第一个事务提交
- **THEN** 两个 Event 各得唯一 version（无版本冲突）

#### Scenario: get_version 无事件返回 0

- **WHEN** 对一个从未写过 Event 的 `session_id` 调用 `get_version(session_id)`
- **THEN** 返回 `0`

#### Scenario: payload 用 JSONB 便于查询

- **WHEN** `Event(payload={"tool": "search", "latency_ms": 42})` 被 append
- **THEN** 落表后 `payload` 列是 JSONB
- **THEN** `SELECT * FROM events WHERE payload->>'tool' = 'search'` 能命中该行

