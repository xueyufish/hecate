## Context

P1 的 CheckpointStore 是 InMemory 实现（`engine/checkpoint.py` Lines 76-126），单进程、不持久。架构文档 AD-5 规划了渐进式扩展：P1 线程池→P2 跨进程→P3 Temporal。PostgreSQL CheckpointStore 是 P2 的关键基础设施。

## Goals / Non-Goals

**Goals:**

1. 实现 PostgresCheckpointStore，复用已有 CheckpointModel ORM
2. 支持跨节点恢复（任何节点可加载 checkpoint）
3. 支持时间旅行（加载任意历史 checkpoint）
4. 内存缓存最近 checkpoint 加速热路径

**Non-Goals:**

1. 不实现分布式调度（属于 P3 Temporal）
2. 不修改 PregelRuntime（纯新增实现）
3. 不删除 InMemoryCheckpointStore（保留用于测试）

## Decisions

### D1: 复用已有 CheckpointModel ORM

**选择**：直接使用 `models/checkpoint.py` 的 CheckpointModel

**理由**：
- 表结构已设计好（session_id, superstep, node_id, channel_state JSONB, metadata JSONB）
- 无需新增 migration
- 与 P1 的 InMemoryCheckpointStore 接口一致

### D2: 最近 checkpoint 内存缓存

**选择**：使用 LRU 缓存最近 N 个 session 的 checkpoint

**理由**：
- 热路径（恢复执行）需要低延迟
- 避免每次都查询数据库
- 缓存一致性通过 write-through 保证

## Risks / Trade-offs

| 风险 | 缓解 |
|------|------|
| 数据库写入延迟 | 异步写入 + WAL 先写日志 |
| 缓存一致性 | write-through 策略 |
| JSONB 数据过大 | 设置 channel_state 大小限制 |
