## 1. 模块结构与数据类

- [x] 1.1 在 `src/hecate/engine/session_state.py` 创建文件，按 `from __future__ import annotations` 开头，导入 uuid、datetime.UTC、datetime、Any、Pydantic BaseModel / ConfigDict / Field
- [x] 1.2 定义 `SessionSummary` frozen Pydantic model（fields: session_id, org_id, user_id, updated_at, superstep | None）
- [x] 1.3 定义 `SessionState` frozen Pydantic model（fields: channel_state, agent_state, event_position, metadata，默认值见 spec）
- [x] 1.4 定义 `SessionNotFoundError(ValueError)` 异常类，构造时接受 `org_id, user_id, session_id` 三个参数生成诊断消息

## 2. SessionStateStore 抽象基类

- [x] 2.1 定义 `SessionStateStore(ABC)` 类，所有方法声明为 `@abstractmethod async def`
- [x] 2.2 定义 `save(org_id, user_id, session_id, state: SessionState) -> None` 方法签名
- [x] 2.3 定义 `load(org_id, user_id, session_id) -> SessionState | None` 方法签名
- [x] 2.4 定义 `list_recent(org_id, user_id, limit: int = 10) -> list[SessionSummary]` 方法签名
- [x] 2.5 添加 ABC 直接实例化防护：试图 `SessionStateStore()` SHALL raise `TypeError`

## 3. InMemorySessionStateStore 实现

- [x] 3.1 实现 `InMemorySessionStateStore(SessionStateStore)` 类，构造函数无参数
- [x] 3.2 实现 `save` 方法：序列化为 JSON 字符串后存到 `_storage[org_id][user_id][session_id] = (json_str, datetime.UTC.now())`
- [x] 3.3 实现 `load` 方法：从 `_storage` 取 JSON 字符串反序列化为 `SessionState`；未找到返回 None
- [x] 3.4 实现 `list_recent` 方法：扫描 `_storage` 收集 `(org_id, user_id, session_id, updated_at)` 元组，过滤匹配 `(org_id, user_id)`，按 `updated_at` desc 排序，返回前 `limit` 个 `SessionSummary`
- [x] 3.5 添加 `_storage` 类型注解：`dict[uuid.UUID, dict[uuid.UUID, dict[uuid.UUID, tuple[str, datetime]]]]`

## 4. MemoryProvider 扩展点预留

- [x] 4.1 在 `session_state.py` 文件顶部添加 docstring 引用 ADR-020 和 MemoryProvider 未来扩展点
- [x] 4.2 在 `SessionStateStore` ABC 上方添加 `MemoryProvider` 扩展点注释块（说明长期记忆与短期 session state 的边界，本 change 不实现）

## 5. 单元测试

- [x] 5.1 在 `tests/test_engine/` 创建 `test_session_state.py` 测试文件
- [x] 5.2 测试 `SessionState` frozen 性质：mutate field SHALL raise `ValidationError`
- [x] 5.3 测试 `SessionState.model_copy(update=...)` 返回新实例且其他字段不变
- [x] 5.4 测试 `SessionState` JSON 序列化往返一致性
- [x] 5.5 测试 `SessionState` 拒绝负数 `event_position`
- [x] 5.6 测试 `SessionState` 默认值（`event_position=0`, `metadata={}`）
- [x] 5.7 测试 `SessionStateStore` ABC 不能直接实例化
- [x] 5.8 测试 `InMemorySessionStateStore.save` + `load` 返回相同 state
- [x] 5.9 测试 `InMemorySessionStateStore.load` 对未知 session 返回 None
- [x] 5.10 测试 `InMemorySessionStateStore.list_recent` 按 `updated_at` desc 排序
- [x] 5.11 测试 `list_recent` org_id 隔离（不同 org 的 session 不互相可见）
- [x] 5.12 测试 `list_recent` user_id 隔离（同一 org 不同 user 的 session 不互相可见）
- [x] 5.13 测试 `list_recent` limit 参数生效
- [x] 5.14 测试 `SessionNotFoundError` 消息包含 `(org_id, user_id, session_id)` 三元组
- [x] 5.15 测试 ABC 所有方法都是 async（`asyncio.iscoroutinefunction` 检查）

## 6. 验证

- [x] 6.1 跑 `ruff check src/hecate/engine/session_state.py tests/test_engine/test_session_state.py` — All checks passed
- [x] 6.2 跑 `ruff format --check src/hecate/engine/session_state.py tests/test_engine/test_session_state.py` — formatted
- [x] 6.3 跑 `mypy src/hecate/engine/session_state.py` — Success: no issues found
- [x] 6.4 跑 `python -m pytest tests/test_engine/test_session_state.py -v` — all pass
- [x] 6.5 跑完整 `python -m pytest tests/test_engine/ -q` — 不破坏现有测试
- [x] 6.6 跑完整 `ruff check src/hecate/ tests/` — All checks passed（确认未引入其他文件回归）