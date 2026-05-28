## Why

P1 的 CheckpointStore 是 InMemory 实现——单进程、不持久、不支持跨节点。P3 Temporal 集成需要 PostgreSQL CheckpointStore 作为前置条件。如果 P2 不做，P3 会返工。

## What Changes

- **新增 `PostgresCheckpointStore`**：实现 `CheckpointStore` 接口，使用 PostgreSQL 持久化 checkpoint
- **支持跨节点恢复**：任何节点都可以加载 checkpoint 恢复执行
- **支持时间旅行**：可加载任意历史 checkpoint 进行调试
- **内存缓存**：最近 checkpoint 缓存在内存，加速热路径

## Capabilities

### New Capabilities

- `postgres-checkpoint`: PostgreSQL 持久化 checkpoint，支持跨节点恢复和时间旅行

### Modified Capabilities

（无）

## Impact

- **新增代码**：`engine/checkpoint.py` 中新增 `PostgresCheckpointStore` 类，约 100 行
- **修改代码**：无（纯新增实现）
- **新增依赖**：无（复用已有 asyncpg + SQLAlchemy）
- **零破坏性变更**：InMemoryCheckpointStore 保留作为测试用
