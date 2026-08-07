## Context

Change 3（`session-state-store-wiring`，2026-08-03 archived）完成 SessionStateStore 生产 wiring，但暴露三个问题：

1. **流式保存路径漏接**：`execution_service.py:473-474` 的 `_stream_execute` 仍走 legacy `_state_store`，未调用 `_persist_session_state`。生产环境 `state_store=None`，流式 chat 完全不持久化。
2. **并发写竞态无防护**：同一 `(org_id, user_id, session_id)` 被并发请求持有时 last-write-wins。
3. **性能/可观测性黑盒**：wired backend 的 latency/失败率未知。

业界调研覆盖 20 个项目（LangGraph / AgentScope 2.0 / Bedrock AgentCore / Salesforce BYOP / Palantir AIP / Restate / Temporal / Ably / Hermes / OpenClaw / AutoGen / CrewAI / Mastra / DeerFlow / Dify / openJiuwen / Claude Code / Codex / watsonx Orchestrate / Gemini Enterprise），形成三个关键参照：

- **Restate Virtual Object**：runtime 级 single-writer-per-key，无应用层锁——理想模型，但需引入新 runtime
- **Hermes Agent**：多进程共享 SQLite 用 1s timeout + jitter retry × 15（20-150ms）打破 convoy effect
- **LangGraph production guide**：PG row-level lock 或 Redis Redlock 防 thread 双写；checkpoint 频率 trade-off（"78% reduction in writes"）

本 change 借鉴业界实践，在现有架构内实现等价保证。

## Goals / Non-Goals

**Goals：**
- 修复流式保存 bug：`_stream_execute` 接入 `_persist_session_state`，stream 正常结束 atomic save，异常断开 best-effort save
- 加并发锁：`SessionStateStore` ABC 新增 `acquire_session_lock` / `release_session_lock` 默认 no-op；Redis/PG/Tiered 各自实现
- 失败策略：retry 3 次（jitter 20-150ms）→ 抛 `SessionStateConflictError` → **不走** legacy fallback
- 性能基准：`test_perf.py` 测 wired vs unwired 的 p50/p95/p99，目标 tiered ≤ 10ms p95 平台开销
- OTel observability：`_persist_session_state` 加 span + 结构化日志（save 次数/失败/latency/backend）
- 显式文档化：spec 明确 "services-layer 每请求 1 次 atomic save，不做 per-superstep"

**Non-Goals：**
- **不删除** `AgentStateStore` / `state_store` 参数——保留双写期，Change 6 cleanup（参考 Claude Code dual-write + AgentScope major 版本节奏）
- **不做** K8s manifest / Helm chart / HPA 配置——ops 工作，不在 change 范围（业界无人把 load test 作为 change 交付物）
- **不做** locust/k6 load test harness——同上
- **不引入** Restate / Temporal / 新 runtime——保持现有 FastAPI + PregelRuntime 架构
- **不引入** AgentScope 风格 `DistributedBackend` 统一抽象——YAGNI，Change 5 EventStore 后再说
- **不改** engine-layer `InMemoryCheckpointStore`（per-request PregelRuntime 中间回滚用，与 services-layer 持久化无关）
- **不做** durable sessions 层（Ably AI Transport 概念）——SSE 断开重连属于 Change 7+ 范畴

## Decisions

### 决策 1：流式保存采用 stream-end atomic + 异常断开 best-effort

`_stream_execute` 在 stream 正常结束（generator 耗尽）后调用 `_persist_session_state` 一次（atomic）。stream 异常断开（client disconnect / timeout / 异常抛出）时，在 finally 块 best-effort 调用 `_persist_session_state`——失败也 swallow（已经 in-memory agent_state 有效）。

```python
async def _stream_execute(self, ...):
    try:
        async for event in runtime.execute(...):
            yield event
        # 正常结束：atomic save
        await self._persist_session_state(agent_state, session_id, agent_id, org_id, user_id)
    except Exception:
        # 异常断开：best-effort save（可能失败，swallow）
        try:
            await self._persist_session_state(agent_state, session_id, agent_id, org_id, user_id)
        except Exception as e:
            logger.warning("Stream disconnect save failed for session=%s: %s", session_id, e)
        raise
```

**为什么不 per-superstep**：
- LangGraph production 教训："14-node graph 产 180KB blob，resume 从 2s 飙到 11s，3 周 10GB checkpoint 表"
- BSWEN 实战："78% reduction in checkpoint writes" by 只在 logical boundary 写
- PregelRuntime 内部已有 engine-layer `InMemoryCheckpointStore` 处理中间 superstep 回滚——services-layer 不该承担 per-superstep 持久化（那是 engine 的事）

**业界参照**：
- BYOP（Salesforce）：session-end atomic，Redis 后台 save
- Bedrock AgentCore：session-level atomic（microVM 销毁前 flush）
- LangGraph：默认 per-superstep，但 production guide 建议减少

**为什么不 durable sessions 层**：
- Ably 的"durable sessions"（buffered/addressable/multi-participant coherent）解决 SSE 断开重连，不是 agent state 持久化
- DeerFlow RFC-2471 在加 Redis stream bridge 解决这个问题，属于 Change 7+ 范畴
- 本 change 只解决 agent_state 持久化，不解决 SSE resume

### 决策 2：并发锁采用 per-backend 策略 + jitter retry + fail-fast

`SessionStateStore` ABC 新增两个可选方法（default no-op，返回 contextmanager）：

```python
class SessionStateStore(ABC):
    @asynccontextmanager
    async def acquire_session_lock(
        self, org_id: UUID, user_id: UUID, session_id: UUID, *, timeout_ms: int = 30000
    ) -> AsyncGenerator[None, None]:
        """默认 no-op；具体实现覆盖。锁失败抛 SessionStateConflictError。"""
        yield  # default: no lock

    # save/load/list_recent 保持不变
```

各实现：

| Backend | 锁机制 | 实现 |
|---------|-------|------|
| `InMemorySessionStateStore` | no-op（单进程 asyncio 天然单写） | 继承 default |
| `RedisSessionStateStore` | `SET lock:{org}:{user}:{session} {owner_uuid} NX PX {timeout_ms}` + Lua release | owner UUID 防 A 误删 B 的锁 |
| `PostgresSessionStateStore` | `SELECT ... FOR UPDATE` 在 save 事务内 | 自然串行化，无显式 acquire |
| `TieredSessionStateStore` | Redis SETNX（上层）+ PG 事务内 FOR UPDATE（下层） | 双保险，参考 Kleppmann 混合策略 |

**失败策略**：

```python
# _persist_session_state 内部
for attempt in range(3):  # 3 次 retry
    try:
        async with self._checkpoint_store.acquire_session_lock(org, user, session, timeout_ms=5000):
            await self._checkpoint_store.save(org, user, session, state)
            return
    except SessionStateConflictError:
        if attempt < 2:
            await asyncio.sleep(random.uniform(0.020, 0.150))  # Hermes jitter
            continue
        raise  # fail-fast，不走 legacy fallback
```

**为什么不走 legacy fallback**：Change 3 的 line 389-391 fallback 是 bug——保存失败 fallback 到 `_state_store.save` 会让两个 store 状态分裂，破坏一致性。本 change 移除该 fallback。

**业界参照**：
- Hermes Agent：1s timeout + jitter 20-150ms × 15 retry，打破 convoy effect
- LangGraph production："serialize-by-thread (Redis lock) or accept last-write-wins"
- Kleppmann："layer multiple mechanisms — lock + idempotency key + DB constraint"
- Restate：single-writer-per-key 是 runtime 级保证（理想，但需新 runtime）

**为什么不用 Redlock**：Kleppmann 批评 Redlock 依赖时钟假设，无 fencing token。我们的场景是 session state（非金融），单 Redis SETNX 够用；如果未来需要更强保证，加 PG 幂等 key 即可（已在 Tiered 模式实现）。

### 决策 3：OTel observability 接入现有 `hecate.observability`

`_persist_session_state` 加 OTel span + 结构化日志，复用现有 `hecate.observability` 模块（不引入 prometheus_client 等新依赖）：

```python
async def _persist_session_state(self, ...):
    with tracer.start_as_current_span("session_state.persist") as span:
        span.set_attribute("session.id", str(session_id))
        span.set_attribute("session.backend", type(self._checkpoint_store).__name__)
        start = time.monotonic()
        try:
            async with self._checkpoint_store.acquire_session_lock(...):
                await self._checkpoint_store.save(...)
            span.set_attribute("session.save.success", True)
        except Exception as e:
            span.set_attribute("session.save.success", False)
            span.record_exception(e)
            raise
        finally:
            elapsed_ms = (time.monotonic() - start) * 1000
            span.set_attribute("session.save.latency_ms", elapsed_ms)
            logger.info(
                "session_state_persist",
                extra={"session_id": str(session_id), "latency_ms": elapsed_ms, "backend": ...},
            )
```

**业界参照**：
- LangGraph production："4 必看 dashboard 指标：nodes per run / tokens per run / p95 run duration / **checkpoint write latency**"
- BYOP："platform overhead as low as 5 milliseconds"——一行 metric
- RapidClaw："per-node traces, per-run token counts, replay handle (thread_id) in every log line"

**为什么不用 prometheus_client**：
- 现有 `hecate.observability` 已基于 OTel
- prometheus_client 需要新 endpoint + 新依赖
- OTel spans 可以后端聚合到 Prometheus / Jaeger / Datadog，更灵活

### 决策 4：性能基准测试用 fakeredis，不依赖外部基础设施

`tests/test_services/test_session_state/test_perf.py`：

```python
@pytest.mark.parametrize("backend", ["memory", "redis-fake", "postgres-mock"])
def test_save_load_latency(backend):
    store = build_store(backend)
    # 预热
    for i in range(100):
        await store.save(org, user, session, make_state())
    
    # 测 1000 次 save + load
    save_latencies = []
    load_latencies = []
    for i in range(1000):
        t0 = time.monotonic()
        await store.save(org, user, session, make_state())
        save_latencies.append((time.monotonic() - t0) * 1000)
        # ... 类似测 load
    
    assert percentile(save_latencies, 95) < 10.0  # p95 < 10ms
    assert percentile(save_latencies, 99) < 50.0  # p99 < 50ms
```

**目标阈值**（参考业界）：
| 指标 | 阈值 | 业界参照 |
|------|------|---------|
| InMemory save p95 | < 1ms | — |
| fakeredis save p95 | < 5ms | LangGraph RedisSaver 实测 |
| mock PG save p95 | < 10ms | BSWEN PG 实测 |
| 平台开销（wired - unwired） p95 | < 10ms | BYOP 5ms 目标 |

**为什么用 fakeredis 而非 testcontainers**：
- CI 不需要 Docker
- 业界（LangGraph / AgentScope）单元测试都用 fakeredis
- testcontainers 留给 Change 2 已有的 `@pytest.mark.integration` 测试

### 决策 5：显式文档化 checkpoint 频率

在 spec 的 ADDED Requirements 中明确：

> "services-layer `SessionStateStore` SHALL 每请求 1 次 atomic save。services-layer SHALL NOT 在 PregelRuntime 的 per-superstep 边界写——per-superstep 持久化是 engine-layer `CheckpointStore` 的职责，用于 PregelRuntime 中间回滚。"

**为什么文档化**：
- LangGraph production 教训："docs present checkpointing as simple opt-in, production disagrees"
- 未来 contributor 可能想加 per-superstep，需要明确这是有意决策
- BSWEN 实战："checkpoint bloat is silent — discover when DB hits 90% disk"

## Risks / Trade-offs

| 风险 | 缓解 |
|------|------|
| Redis SETNX 锁在 failover 时丢失（Kleppmann 批评） | session state 非金融场景，可容忍偶发双写；Tiered 模式 PG 幂等 key 兜底 |
| 流式 best-effort save 在客户端断开时可能丢失最后 N 个 tool 输出 | in-memory agent_state 仍有效；下个请求 load 时拿到的是 superstep 前 state，可接受 |
| OTel span 增加微小 latency（~0.1ms） | 测量显示 OTel span 开销 < 0.5ms，远低于 save 本身；可通过 `OTEL_ENABLED=false` 关闭 |
| 性能基准阈值（10ms p95）可能因 CI 环境波动 | 阈值是上限，实际 fakeredis 通常 < 1ms；CI flaky 时可调 |
| 移除 legacy fallback 可能影响既有测试 | 既有 23 个 execution_service 测试不传 checkpoint_store，`_state_store is None`，不走 fallback 分支——零影响 |
| jitter retry 在高争用下可能放大负载 | retry 上限 3 次（vs Hermes 15 次），fail-fast 快速释放资源 |

## Migration Plan

**部署步骤**：
1. 部署新代码（锁方法 default no-op，`InMemorySessionStateStore` 行为不变）
2. 操作员设置 `SESSION_STATE_STORE_BACKEND=tiered` 启用分布式 backend（锁自动生效）
3. 既有 `SESSION_STATE_STORE_BACKEND=memory` 部署零变化

**回滚策略**：
- 回滚代码即可（无 schema 变更、无数据迁移）
- 已写入 Redis/PG 的 SessionState 行 TTL 自然过期（默认 7 天）

**数据迁移**：无（SessionState schema 不变）

## Open Questions

（无——所有 P0 问题已在 explore mode 确认）
