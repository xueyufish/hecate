## 1. AgentState Data Model — AgentState 数据模型

- [x] 1.1 创建 `src/hecate/services/state/__init__.py` — 导出 AgentState、AgentStateStore、InMemoryStateStore
- [x] 1.2 创建 `src/hecate/services/state/state.py` — AgentState Pydantic 模型，字段：session_id（UUID）、agent_id（UUID）、summary（str）、context（list[dict]）、permission_context（dict）、tool_context（dict）、task_context（dict）、environment_root（str | None）、metadata（dict）

## 2. AgentStateStore

- [x] 2.1 创建 `src/hecate/services/state/store.py` — AgentStateStore ABC，包含抽象方法：save(agent_id, session_id, state)、load(agent_id, session_id)、delete(agent_id, session_id)、list_sessions(agent_id)
- [x] 2.2 在 store.py 中实现 InMemoryStateStore — 基于字典的存储，每个会话键使用 asyncio.Lock 保证并发安全

## 3. WorkflowExecutionService Integration — WorkflowExecutionService 集成

- [x] 3.1 向 WorkflowExecutionService.__init__ 添加 `state_store: AgentStateStore | None = None` 参数
- [x] 3.2 在 execute() 入口添加状态加载 — 加载现有状态或创建新的 AgentState，注入到 execution_context["_agent_state"]
- [x] 3.3 在 execute() 退出时添加状态保存 — 在 _non_stream_execute 和 _stream_execute 完成后保存 AgentState
- [x] 3.4 从 EnvironmentManager（如果可用）填充 AgentState.environment_root

## 4. Tests — 测试

- [x] 4.1 测试 AgentState 模型 — 使用默认值创建、使用显式值创建、model_dump 往返、从字典 model_validate
- [x] 4.2 测试 InMemoryStateStore — 保存/加载、加载不存在返回 None、删除、list_sessions、不同会话独立
- [x] 4.3 测试 InMemoryStateStore 并发安全 — 两个协程保存相同键，无损坏
- [x] 4.4 测试 WorkflowExecutionService 集成 — 入口加载状态、退出保存状态、跨调用持久化、environment_root 填充

## 5. Verification — 验证

- [x] 5.1 运行 `ruff check src/hecate/ tests/ && ruff format --check src/ tests/` — 0 错误
- [x] 5.2 运行 `mypy src/` — 0 错误
- [x] 5.3 运行 `python -m pytest tests/test_services/test_state/ -q` — 全部通过
