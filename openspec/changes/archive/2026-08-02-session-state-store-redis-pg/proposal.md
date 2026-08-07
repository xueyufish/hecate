## Why

Change 1（2026-08-01 archived）在 engine 层完成了 `SessionStateStore` 抽象 + `InMemorySessionStateStore`。但 in-memory 实现只对单进程测试/开发有用——生产环境需要分布式后端才能支持水平扩展（13.4）。当前所有 checkpoint / agent_state / event position 数据在请求结束即丢失，无法支撑任何多副本部署。

本 change 实现 Change 1 抽象的两种生产后端 + 一层组合：RedisSessionStateStore（热路径）、PostgresSessionStateStore（durable 真相源）、TieredSessionStateStore（write-through + Redis 故障降级）。后续 Change 3 会把生产路径从 `InMemoryCheckpointStore()` 切换到 `TieredSessionStateStore`。

## What Changes

- 新增 `src/hecate/services/session_state/redis_store.py`：`RedisSessionStateStore` 实现，使用 `redis.asyncio` 异步客户端
- 新增 `src/hecate/services/session_state/postgres_store.py`：`PostgresSessionStateStore` 实现，使用 SQLAlchemy ORM 写 `session_states` 表（JSONB）
- 新增 `src/hecate/services/session_state/tiered_store.py`：`TieredSessionStateStore` 组合 Redis + PG，写穿透 + Redis 故障降级 + read-through + cache miss fallback
- 新增 `src/hecate/services/session_state/factory.py`：按 `SESSION_STATE_STORE_BACKEND` 配置选择后端
- 新增 `src/hecate/services/session_state/models.py`：`SessionStateModel` SQLAlchemy ORM + Alembic migration
- 新增 `pyproject.toml` 的 `[redis]` optional dependency group，声明 `redis>=5.0` 依赖
- 新增 `src/hecate/core/config.py` 配置项：`SESSION_STATE_STORE_BACKEND`、`SESSION_STATE_TTL_DAYS`、`SESSION_STATE_REDIS_URL`、`SESSION_STATE_PG_TABLE`、`SESSION_STATE_KEY_PREFIX`
- 新增测试：单元（fakeredis + mock PG）+ 集成（testcontainers 真 PG/Redis），用 `pytest.mark.integration` 标记
- 新增 Alembic migration 文件：`alembic/versions/<hash>_add_session_states_table.py`

## Capabilities

### Modified Capabilities
- `distributed-session-state-store`: 在已有 `SessionStateStore` ABC 之上，新增 Redis/Postgres/Tiered 三种生产实现 + factory。具体的 read-through、write-through、Redis 故障降级、TTL、多租户 key 等行为作为 ADDED Requirements 加入。

### New Capabilities
（无）

## Impact

- **新增文件**：`src/hecate/services/session_state/{redis_store,postgres_store,tiered_store,factory,models}.py`（约 600 行）、`alembic/versions/..._add_session_states_table.py`（约 50 行）
- **新增测试**：`tests/test_services/test_session_state/{test_redis_store,test_postgres_store,test_tiered_store,test_factory}.py`（约 800 行）
- **修改文件**：`pyproject.toml`（新增 `[redis]` group）、`src/hecate/core/config.py`（新增 5 个 settings）
- **运行时依赖**：新增 `redis>=5.0`（仅当 settings `SESSION_STATE_STORE_BACKEND` 启用 redis/tiered 时才实际加载——lazy import）
- **不修改**：Change 1 的 engine 层抽象（`src/hecate/engine/session_state.py` 完全不变）
- **后续依赖**：Change 3（`session-state-store-wiring`）会消费本 change 提供的 factory
- **CI 变更**：集成测试需要 docker，可在 main merge 前运行（nightly job 或 main-only trigger）