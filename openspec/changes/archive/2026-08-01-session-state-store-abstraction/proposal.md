## Why

Hecate 目前的状态持久化是断的：`execution_service.py:281` 每个请求都创建 `checkpoint_store = InMemoryCheckpointStore()`——checkpoints 不持久化、不跨进程、不跨请求。`AgentStateStore` 在 chat API 完全没被接线，`EventStore` 也只在 InMemory 模式。这种状态让 Hecate 无法真正支撑水平扩展（feature 13.4 + 13.4a）——任何 replica 崩溃或重启都会让 session 丢失。

本 change 是 5-change 拆分（p3-mvp-audit.md 第 260 行 P0 发布阻塞项）的第一步：在 engine 层引入统一的 `SessionStateStore` 抽象，把三套分裂的 store（CheckpointStore / AgentStateStore / EventStore）通过 `SessionState` 数据结构收拢。后续 4 个 change 依次实现 Redis/PostgreSQL 后端、接线到生产链路、验证多副本、EventStore PG 持久化。

## What Changes

- 新增 `src/hecate/engine/session_state.py`：定义 `SessionState` frozen dataclass（Pydantic frozen），聚合 channel_state、agent_state、event_position、metadata
- 新增 `SessionStateStore` ABC（save/load/list_recent），设计 `(org_id, user_id, session_id)` 三元组 key
- 新增 `InMemorySessionStateStore` 实现（engine 层 zero-external-deps，jsonschema 例外）
- 抽象边界：保留现有 `CheckpointStore` 和 `EventStore` ABC 作为独立抽象（不强行合并），但 SessionState 包含 event_position 字段
- 为后续 change 预留扩展点：`MemoryProvider`（长期记忆层，openJiuwen L0-L3 模式，本 change 不实现）

## Capabilities

### New Capabilities
- `distributed-session-state-store`: SessionStateStore 抽象 + SessionState 数据结构 + InMemory 实现，覆盖 13.4a 的核心 engine 层 API

### Modified Capabilities
（无——本次仅新增抽象，不修改 CheckpointStore/EventStore 现有 spec；后续 change 才接入生产路径）

## Impact

- **新增文件**：`src/hecate/engine/session_state.py`（含 ABC + 数据类 + InMemory 实现）
- **新增测试**：`tests/test_engine/test_session_state.py`
- **无破坏性**：本 change 不修改 CheckpointStore、AgentStateStore、EventStore 任何现有代码
- **后续 change**：`session-state-store-redis-pg`（实现生产后端）、`session-state-store-wiring`（接线生产）、`horizontal-scaling-validation`（验证）、`eventstore-pg-wiring`（EventStore PG 持久化）
- **依赖**：本 change 是其他 4 个 change 的前置；engine 层无新增依赖（redis/asyncpg 由 Change 2 引入）