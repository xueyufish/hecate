## 1. SessionStateStore ABC 锁接口

- [x] 1.1 在 `src/hecate/engine/session_state.py` 顶部导入 `asynccontextmanager`、`AsyncGenerator`、`random`（如尚无）
- [x] 1.2 新增异常 `SessionStateConflictError(Exception)`，`__init__` 接收 `(org_id, user_id, session_id)` 三元组，消息格式 `"Session state lock contention for org=%s user=%s session=%s"`
- [x] 1.3 在 `SessionStateStore` ABC 上新增 `acquire_session_lock` 异步上下文管理器方法，签名 `async def acquire_session_lock(self, org_id: uuid.UUID, user_id: uuid.UUID, session_id: uuid.UUID, *, timeout_ms: int = 30000) -> AsyncGenerator[None, None]`，default 实现 `yield`（no-op）
- [x] 1.4 用 `@asynccontextmanager` 装饰 default 实现
- [x] 1.5 验证：`InMemorySessionStateStore` 继承 default no-op，不改

## 2. RedisSessionStateStore 锁实现

- [x] 2.1 在 `src/hecate/services/session_state/redis_store.py` 导入 `uuid as uuid_lib`、`random`、`asyncio`
- [x] 2.2 在 `RedisSessionStateStore` 上覆盖 `acquire_session_lock`，签名为 async context manager
- [x] 2.3 实现获取锁逻辑：`lock_key = f"{self._key_prefix}lock:{{{org_id}}}:{user_id}:{session_id}"`（注意 `{org_id}` hash tag for cluster），`owner = str(uuid_lib.uuid4())`，retry 3 次（jitter `random.uniform(0.020, 0.150)`），每次 `await self._redis.set(lock_key, owner, nx=True, px=timeout_ms)`
- [x] 2.4 SET 成功 break 出 retry 循环；3 次都失败抛 `SessionStateConflictError(org_id, user_id, session_id)`
- [x] 2.5 实现 release：用 Lua 脚本 `if redis.call("get", KEYS[1]) == ARGV[1] then return redis.call("del", KEYS[1]) else return 0 end`，注册为 `self._release_script = self._redis.register_script(...)`
- [x] 2.6 在 `@asynccontextmanager` 的 try/finally 中调用 release（确保异常也释放）
- [x] 2.7 验证：单元测试 `test_redis_lock_acquire_and_release`、`test_redis_lock_contention_raises_conflict`、`test_redis_lock_release_owner_mismatch_no_delete`

## 3. PostgresSessionStateStore 行锁

- [x] 3.1 在 `src/hecate/services/session_state/postgres_store.py` 的 `save` 方法内，UPSERT 前先 `SELECT ... FOR UPDATE` 锁定既有行
- [x] 3.2 用 `session.execute(select(SessionStateModel).where(...).with_for_update())` 实现；行不存在时 SELECT 返回空，正常走 INSERT ON CONFLICT
- [x] 3.3 `acquire_session_lock` 方法**不**覆盖（继承 ABC default no-op）—— PG 行锁在 save 事务内自然生效
- [x] 3.4 验证：单元测试 `test_pg_save_locks_row_for_update`、`test_pg_concurrent_save_serializes`（用 asyncio.gather 模拟并发）

## 4. TieredSessionStateStore 双保险

- [x] 4.1 在 `src/hecate/services/session_state/tiered_store.py` 覆盖 `acquire_session_lock`，委托给 `self._redis_store.acquire_session_lock(...)`
- [x] 4.2 Redis 异常时 log warning 并 swallow，继续 yield（fallback 到 PG 行锁兜底）
- [x] 4.3 验证：单元测试 `test_tiered_lock_redis_success`、`test_tiered_lock_redis_failure_fallback`、`test_tiered_lock_both_healthy_double_protection`

## 5. 修复流式保存路径

- [x] 5.1 在 `src/hecate/services/workflow/execution_service.py` 的 `_stream_execute` 方法内，移除 line 473-474 的 `if self._state_store and agent_state: await self._state_store.save(...)` 分支
- [x] 5.2 把 generator 主体包在 try/except/finally 中：try 块正常 yield 所有 event 后调用 `_persist_session_state`；except 块 best-effort 调用 `_persist_session_state`（包在 inner try/except 中 swallow 失败）后重抛原异常
- [x] 5.3 验证：`_stream_execute` 不再引用 `self._state_store`
- [x] 5.4 在 `src/hecate/services/workflow/execution_service.py` 的 `_persist_session_state` 方法内，移除 line 389-391 的 `if self._state_store and agent_id and agent_state and session_id: await self._state_store.save(...)` legacy fallback 分支

## 6. _persist_session_state 锁调用 + jitter retry

- [x] 6.1 在 `_persist_session_state` 方法顶部导入 `random`、`asyncio`（如尚无），导入 `SessionStateConflictError`
- [x] 6.2 用 retry 循环（3 次）包裹 `acquire_session_lock + save`：`for attempt in range(3):` … `except SessionStateConflictError: if attempt < 2: await asyncio.sleep(random.uniform(0.020, 0.150)); continue; raise`
- [x] 6.3 retry 循环放在 `if self._checkpoint_store is not None and ...` 块内
- [x] 6.4 验证：单元测试 `test_persist_retry_then_success`（前 2 次 lock 失败，第 3 次成功）、`test_persist_retry_3_times_fail_propagates`、`test_persist_no_legacy_fallback`

## 7. OTel observability 钩子

- [x] 7.1 在 `execution_service.py` 顶部导入 `from opentelemetry import trace`（如尚无）；导入 `time`
- [x] 7.2 在 `_persist_session_state` 方法内用 `tracer.start_as_current_span("session_state.persist") as span:` 包裹 retry + save 主体
- [x] 7.3 设置 span 属性：`session.id`、`session.backend`（`type(self._checkpoint_store).__name__`）、`session.lock.acquired`、`session.save.success`、`session.save.latency_ms`
- [x] 7.4 失败路径调 `span.record_exception(e)`
- [x] 7.5 在 finally 块输出结构化日志：`logger.info("session_state_persist", extra={"session_id": ..., "latency_ms": ..., "backend": ..., "success": ...})`
- [x] 7.6 复用 `hecate.observability` 模块的 tracer 单例；如不存在则 fallback 到 `trace.get_tracer(__name__)`
- [x] 7.7 验证：单元测试 `test_persist_emits_otel_span`（用 `span_exporter` 收集验证属性）、`test_persist_emits_structured_log`

## 8. 并发锁正确性测试

- [x] 8.1 创建 `tests/test_services/test_session_state/test_concurrency_lock.py`
- [x] 8.2 测试 Redis SETNX：`test_redis_lock_mutual_exclusion`（两个并发 acquire，第二个 retry 失败抛 ConflictError）
- [x] 8.3 测试 Redis Lua release：`test_redis_lock_release_with_owner_mismatch`（A 的 release 不能删 B 的锁）
- [x] 8.4 测试 PG `SELECT FOR UPDATE`：`test_pg_row_lock_serializes_concurrent_saves`（两个并发 save，第二个等第一个）
- [x] 8.5 测试 Tiered：`test_tiered_redis_failure_falls_back_to_pg_lock`（mock Redis 故障，验证 PG 兜底）
- [x] 8.6 测试 end-to-end：`test_persist_session_state_concurrent_no_data_loss`（两个并发 `_persist_session_state` 调用，第二个 retry 后第一个已写入，最终 state 是后写者，无中间丢失）

## 9. 性能基准测试

- [x] 9.1 创建 `tests/test_services/test_session_state/test_perf.py`
- [x] 9.2 实现 `percentile(samples, p)` helper
- [x] 9.3 参数化测试 `test_save_latency` 跑 1000 次 save，对比 InMemory / fakeredis / mock PG
- [x] 9.4 测试 `test_load_latency` 跑 1000 次 load
- [x] 9.5 测试 `test_platform_overhead`：`(wired_latency - unwired_latency)` p95 < 10ms
- [x] 9.6 阈值断言：InMemory save p95 < 1ms、fakeredis save p95 < 5ms、mock PG save p95 < 10ms
- [x] 9.7 在 `pyproject.toml` 的 pytest 配置加 `markers = ["perf: performance benchmarks"]`（如尚无）；test_perf.py 用 `@pytest.mark.perf` 标记（不放进默认测试套件，避免 CI flaky）

## 10. 流式保存回归测试

- [x] 10.1 创建 `tests/test_services/test_workflow/test_streaming_save.py`
- [x] 10.2 测试 `test_stream_normal_end_persists_session_state`：用 mock PregelRuntime 跑完 stream，验证 `_checkpoint_store.save` 被调用恰好一次
- [x] 10.3 测试 `test_stream_client_disconnect_best_effort_save`：模拟 client disconnect（generator 中途抛异常），验证 best-effort save 被尝试，原异常重抛
- [x] 10.4 测试 `test_stream_no_legacy_state_store_call`：验证 `_state_store.save` 在 stream 路径中不被调用（即便 `state_store` 提供）
- [x] 10.5 测试 `test_stream_persist_failure_does_not_block_stream`：mock `_persist_session_state` 抛异常，验证 stream 输出不受影响（best-effort）

## 11. 验证

- [x] 11.1 跑 `ruff check src/hecate/services/workflow/execution_service.py src/hecate/engine/session_state.py src/hecate/services/session_state/ tests/test_services/test_session_state/test_concurrency_lock.py tests/test_services/test_session_state/test_perf.py tests/test_services/test_workflow/test_streaming_save.py` — All checks passed
- [x] 11.2 跑 `ruff format --check` 上述路径 — formatted
- [x] 11.3 跑 `mypy src/hecate/services/workflow/execution_service.py src/hecate/engine/session_state.py src/hecate/services/session_state/` — Success: no issues found
- [x] 11.4 跑 `python -m pytest tests/test_services/test_session_state/ tests/test_services/test_workflow/ -v` — 所有新测试 + 既有测试全过
- [x] 11.5 跑 `python -m pytest tests/ -q` — 不破坏现有测试（既有 23 个 execution_service 测试零修改通过）
- [x] 11.6 跑 `python -m pytest tests/test_services/test_session_state/test_perf.py -v` — 性能阈值满足
- [x] 11.7 跑完整 `ruff check src/hecate/ tests/` — All checks passed
