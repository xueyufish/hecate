## Context

Change 1 (`session-state-store-abstraction`, archived 2026-08-01) 在 engine 层定义了 `SessionStateStore` ABC + `SessionState` 数据类 + `InMemorySessionStateStore` 单进程实现。生产环境需要分布式后端——这是 Change 2。

**当前 codebase 现状**：
- `src/hecate/engine/session_state.py`：ABC 已就绪（零外部依赖），所有 ABC 方法 `async def`、JSON 序列化契约已锁定
- `src/hecate/model_hub/cache.py`：项目里**已存在 Redis 客户端使用模式**（lazy import、`redis.asyncio.from_url`、`decode_responses=True`、`ping()` health check、`scan_iter` for pattern）——可作为本 change 的代码风格参考
- `src/hecate/core/config.py`：用 pydantic-settings，settings 字段命名约定 `BACKUP_*`、`SCHEDULE_*`
- `src/hecate/services/checkpoint_store.py`：现有 `PostgresCheckpointStore` 实现使用 SQLAlchemy async session factory + LRU 内存缓存——可作为本 change PG 实现的代码风格参考
- `src/hecate/models/checkpoint.py`：`CheckpointModel` 用 ORM 风格，列名为 SQL 而非 Python（`metadata_` → `metadata`）——本 change 的 `SessionStateModel` 应遵循同一模式
- `pyproject.toml`：所有 Redis 包未声明；AGENTS.md 规定新依赖必须放入正确 optional group

**架构基础**（已锁定，详见 Change 1 archived design.md）：
- write-through（PG 真相源 + Redis 缓存）
- `(org_id, user_id, session_id)` 三元组 key
- TTL 默认 7 天 idle
- JSON-only 序列化（Pydantic `model_dump_json`）

## Goals / Non-Goals

**Goals：**
- 提供三种生产可用的 `SessionStateStore` 实现：`RedisSessionStateStore`、`PostgresSessionStateStore`、`TieredSessionStateStore`
- 提供 `factory.py` 按 settings 选择后端，支持 `memory / redis / postgres / tiered` 四种配置值
- Redis 实现：单实例连接池 + key 前缀 + TTL + 故障时 warning 不抛
- PG 实现：ORM 模型 + JSONB 列 + Alembic migration + 失败时抛
- Tiered 实现：write-through + read-through + Redis 故障降级 + lazy warming
- 新建 `[redis]` optional dependency group，声明 `redis>=5.0` + `fakeredis>=2.20`
- 新增 5 个 settings 配置项
- 单元测试用 fakeredis + mock PG（默认 CI），集成测试用 testcontainers 真 PG/Redis（main-only trigger）

**Non-Goals：**
- 不修改 Change 1 的 engine 层抽象（`src/hecate/engine/session_state.py` 不动一行）
- 不接入生产路径（Change 3 `session-state-store-wiring` 范围）
- 不实现 EventStore PG 持久化（独立 change）
- 不实现 MemoryProvider 长期记忆
- 不实现 Redis Cluster 跨 slot 操作的完整性（仅支持单 slot 内的 list_recent；跨 org 的 list_recent 在 Cluster 模式下不保证）
- 不实现自动 TTL 清理 cron job（PG 旧行清理留给 ops；Redis 用 EX 自动过期）

## Decisions

### Decision 1: 三个独立实现 + factory（不只写 Tiered）

**Why：** 测试和单 backend 部署场景需要独立实现。
- 只用 Redis（开发环境 / 中小规模）：`RedisSessionStateStore` 单一后端
- 只用 PG（合规要求 / 审计场景）：`PostgresSessionStateStore` 单一后端
- 生产推荐：`TieredSessionStateStore`（Redis 热路径 + PG 真相源）

**替代方案：** 只写 `TieredSessionStateStore`，省略单一实现
- **拒绝理由：** 测试会复杂化（必须 mock 双方）；单 backend 部署场景需要绕过 tiered 的额外逻辑；用户希望灵活选择。

### Decision 2: 写策略 write-through（不是 write-behind）

**Why：** Hecate 写频率不高（每 agent 对话 1-N 次 superstep 边界写），PG sync INSERT 在 5000/s 量级足够。
- 业界主流（LangGraph / Dify / ADK / AgentScope / openJiuwen 都是 sync write-through）
- write-behind 会引入数据丢失窗口（进程崩溃时未刷盘的 PG 写入丢失）+ 双套状态机（cache 和 truth 一致性维护）+ 复杂故障恢复（dirty session 重新加载逻辑）

**替代方案：** write-behind batch
- **拒绝理由：** 复杂度高，Hecate 不需要这种吞吐。

### Decision 3: Redis 故障降级 + PG 失败报错

```
save(state):
  1. Redis SET (sync, ex=ttl, 失败 → warning 日志 + 继续)
  2. PG INSERT/UPSERT (sync, 失败 → 抛 RuntimeError)
  3. 返回

load(key):
  1. Redis GET → hit → 返回 + 异步 refresh TTL（如果 ttl 配置了 refresh_on_read）
  2. miss → PG SELECT → Redis SET 写回 → 返回
```

**Why：**
- PG 是真相源——PG 失败必须让请求失败（业务行为正确）
- Redis 是缓存——故障不阻塞（PG 仍能服务）
- 与 ADR-020 决策一致

**替代方案：** Redis 写失败抛异常（fall back to PG-only）vs 静默成功
- **拒绝理由：** 静默成功更友好；故障时降级到 PG-only 仍能服务 session；用户感知不到延迟尖峰。

### Decision 4: TTL 实现

- **Redis**：`SET key value EX <seconds>` 自动过期
- **PG**：`updated_at < NOW() - INTERVAL '<ttl_days> days'` 查询时过滤（应用层）；不自动 DELETE 旧行（让 ops 用 cron / backup scheduler 清理，参考 13.5 备份保留策略）

**Why：** Redis 适合自动过期；PG 适合人工保留（合规 / 审计可能要求保留 N 天）。

**替代方案：** PG 用定时 cron DELETE
- **拒绝理由：** 增加部署复杂度（需要 scheduler + 单独 change）；应用层过滤已能满足语义。

### Decision 5: Key 格式 `hecate:state:{org_id}:{user_id}:{session_id}`

**Why：**
- `hecate:state:` 前缀让 Redis Cluster 多应用共享时通过 SCAN MATCH 隔离
- `{org_id}` 作为 Redis Cluster hash tag —— 同 org 数据落同 slot，保证 list_recent 在 Cluster 模式下能用
- 三元组 `(org_id, user_id, session_id)` 是 Change 1 锁定的多租户隔离契约

**替代方案：** 平铺 key 不带 hash tag
- **拒绝理由：** Redis Cluster 下 org_a 和 org_b 的 key 落到不同 slot，跨 slot 的 SCAN 无法工作，list_recent 在 Cluster 模式下完全失效。

### Decision 6: PG ORM 模型 + JSONB 列 + Alembic migration

```python
class SessionStateModel(Base):
    __tablename__ = "session_states"
    org_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    session_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    state: Mapped[dict] = mapped_column(JSON)  # Pydantic dict
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now())
    superstep: Mapped[int | None] = mapped_column(nullable=True)
```

```sql
CREATE INDEX idx_session_states_org_user_updated
    ON session_states (org_id, user_id, updated_at DESC);
```

**Why：**
- 与 Hecate 现有 ORM 风格一致（参考 `CheckpointModel`）
- JSONB 列允许 PostgreSQL 直接 query JSON 内容（未来如要按 metadata 字段索引）
- DESC 索引支持 `list_recent` 的 ORDER BY 性能

**替代方案：** 原生 SQL / `text()` + 手写 SQL
- **拒绝理由：** ORM 模式统一，模型即文档，alembic 自动管理 migration。

### Decision 7: 依赖组 `[redis]`，引入 `redis>=5.0` + `fakeredis>=2.20`

**Why：**
- Redis 在 Hecate 是 session state 专用，不是 scheduling 范畴（虽然 `[scheduling]` 已有 `apscheduler`、`croniter`）
- 未来可能多个 module 都依赖 Redis（cache、queue、session）—— 独立 group 清晰
- `fakeredis` 是测试依赖，放 `[dev]` 还是 `[redis]`？—— 选择 `[redis]`，因为它是 `redis` 的内存实现版本（生产/测试同源）

**替代方案：** 放 `[scheduling]` 或主依赖
- **拒绝理由：** `[scheduling]` 语义不准；主依赖对自部署用户太重（Redis 是 optional infra）。

### Decision 8: 测试策略——fakeredis + mock PG（默认）+ testcontainers 集成测试

**Why：**
- 单元测试要快（CI 主流程必须 < 5min），fakeredis 提供内存版 Redis 模拟，async API 完整
- AGENTS.md 规定"never connect to real PostgreSQL in unit tests"——单元测试 mock PG；集成测试用 testcontainers 真 PG
- 集成测试仅在 main merge 前 / nightly 跑，避免拖慢 PR 流程

**替代方案：** 全部用 testcontainers
- **拒绝理由：** CI 慢、Docker 依赖重、对本地开发不友好。

### Decision 9: Redis Cluster 跨 slot 限制——明确声明不支持

**Why：** Redis Cluster 模式下，跨 slot 的 SCAN / MULTI / 事务不支持。我们把 hash tag 限定在 `{org_id}`，所以：
- 同 org 内的所有 key 在同一 slot
- `list_recent(org_id, user_id)` 在同 slot 内可用
- 跨 org 的 list_recent 不支持（应用不需要）

spec 必须显式记录这个限制。

**替代方案：** 用 Redis Sorted Set 做跨 org 索引
- **拒绝理由：** 增加复杂度；实际场景下每个 user 通常只属于一个 org。

## Risks / Trade-offs

**[Risk 1]** `redis.asyncio` 库版本兼容性——`redis-py` v4 → v5 API 有 breaking changes（async client 重命名、cluster client 重写）
**Mitigation：** 锁版本 `redis>=5.0,<6.0`；CI 用具体小版本验证。

**[Risk 2]** PG JSONB 大对象性能——conversation buffer 几十 MB 时，每个 save 是整 JSON 写
**Mitigation：**
- 第一版接受这个开销（Hecate 单 session 状态通常 < 10MB）
- 未来可加"channel_state 和 agent_state 分开存储"（参考 OpenHands base_state + events 分层）作为独立 change

**[Risk 3]** Redis Cluster 单 org 限制——单 org 数据增长到单 slot 容量上限（Redis Cluster 单 slot 默认 256MB max）会导致失败
**Mitigation：**
- 7 天 TTL 主动过期
- 单 session 通常 < 10MB
- 单 org session 数 × 10MB 应该远小于 256MB
- 如超限可换 Redis sharding 模式或拆分 org

**[Risk 4]** Redis 故障降级期间 cache miss 全部走 PG——突发 Redis 故障会让 PG 流量激增
**Mitigation：**
- Tiered 的 Redis 故障检测用 try/except 包装，每次 save 失败记录 metric
- 监控 cache hit rate（Change 4 验证阶段会暴露 Prometheus 指标）

**[Risk 5]** Redis 客户端连接池——多副本部署时，如果 settings 配置错（如连接池太小），高峰时会卡
**Mitigation：** 使用 `redis.asyncio.ConnectionPool` 默认配置；文档说明如何调优（pool_size、socket_timeout）。

**[Risk 6]** Alembic migration 顺序——如果生产已经有 checkpoint 表，session_states 是新表无冲突
**Mitigation：** 独立 migration 文件，按时间戳生成；Alembic 自动按顺序执行。

**[Risk 7]** `fakeredis` 不完全支持所有 Redis 命令——若 spec 要求 RedisJSON 或某些高级命令，fakeredis 可能 mock 不了
**Mitigation：** spec 仅要求 GET / SET / EXPIRE / DEL 这几个基本命令，fakeredis 完全支持。

## Migration Plan

本 change 是纯新增，无破坏性：
1. 新增 `pyproject.toml` 的 `[redis]` group
2. 新增 `src/hecate/core/config.py` 的 5 个 settings（带默认值，单实例部署无变化）
3. 新增 `alembic/versions/<hash>_add_session_states_table.py`
4. 新增 `src/hecate/services/session_state/` 5 个文件
5. 新增 4 个测试文件（单元 + 集成标记）
6. CI：现有测试不破坏（pg_dump 等不动）；新集成测试需 docker（main-only trigger）

部署策略：
- 默认 backend 仍是 `memory`（单进程），向后兼容
- 用户显式设置 `SESSION_STATE_STORE_BACKEND=tiered` 才启用新后端
- 单实例小规模部署继续用 `memory` 即可

回滚策略：删除新增文件 + 删除 settings 默认值即可，无破坏。

### Decision 10: 集成测试 CI 触发时机——main-only push trigger

集成测试（testcontainers）默认跳过（`RUN_INTEGRATION_TESTS=1` 开启），仅在 main merge 前运行：
- `.github/workflows/ci.yml` 现有 PR job 保持不变
- `.github/workflows/ci.yml` 加 main-only trigger：`if: github.event_name == 'push' && github.ref == 'refs/heads/main'`，跑 `RUN_INTEGRATION_TESTS=1 pytest tests/test_services/test_session_state/test_integration_*.py`

**Why：** 集成测试需要 docker，本地开发不便；PR 跑太慢。nightly cron 太重，main-only trigger 性价比最高。

### Decision 11: Redis Cluster 集成测试——不做

Redis Cluster 行为（hash tag 路由、跨 slot 限制）由 unit 测试的 fakeredis + key 格式验证。testcontainers 不支持 Cluster 模式，需要多 Redis 容器 + cluster 配置，复杂度远超价值。

**Why：** Cluster 实际行为验证留给 Change 4（horizontal-scaling-validation）的 K8s 部署阶段，那里有真实的 Redis Cluster 或 Sentinel 环境。Change 2 仅在 unit 层验证 key 格式正确性（`hecate:state:{org_id}:...`）。

### Decision 12: Alembic migration 命名——`z` 前缀

Alembic migration 文件名采用 `z8b9c0d1e2f3_add_session_states_table.py`，`z` 前缀与现有 `z4f5a6b7c8d9_add_backup_records.py`（data-backup-recovery）保持一致。

**Why：** Hecate 用 `z` 前缀让新表 migration 在文件排序中靠后，便于将来区分基础表 migration（早期）和扩展表 migration（后期）。