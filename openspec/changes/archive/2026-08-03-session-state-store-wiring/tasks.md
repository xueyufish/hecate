## 1. 模块结构与依赖函数

- [x] 1.1 在 `src/hecate/core/deps_state_store.py` 创建文件，按 `from __future__ import annotations` 开头，导入 FastAPI `Request`、`hecate.services.session_state` 的 `create_session_state_store`、`hecate.core.config` 的 `settings`、`hecate.engine.session_state` 的 `SessionStateStore`
- [x] 1.2 实现 `get_session_state_store(request: Request) -> SessionStateStore`：先 `getattr(request.app.state, "session_state_store", None)`；若为 None 则 fallback 到 `create_session_state_store(settings)`；返回 `SessionStateStore`

## 2. WorkflowExecutionService 构造函数扩展

- [x] 2.1 在 `src/hecate/services/workflow/execution_service.py` 的 `__init__` 增加可选参数 `checkpoint_store: SessionStateStore | None = None`（放在 `state_store` 之后）
- [x] 2.2 在 `__init__` 函数体中增加 `self._checkpoint_store = checkpoint_store`
- [x] 2.3 在 `state_store` 字段的 docstring 加 `.. deprecated::` 标记（指向 SessionStateStore）

## 3. execute() 方法——读取路径

- [x] 3.1 在 `src/hecate/services/workflow/execution_service.py` 的 execute() 中，line 215-218 的 `if self._state_store and agent_id: agent_state = await self._state_store.load(...)` 替换为：当 `self._checkpoint_store is not None` 时执行 `state = await self._checkpoint_store.load(org_id, user_id, session_id)` 并 `agent_state = AgentState.model_validate(state.agent_state)` 用 try/except 包裹（失败 fallback 新建 AgentState）；当 `self._checkpoint_store is None` 时保留旧 `self._state_store` 路径
- [x] 3.2 验证：在没有 checkpoint_store 时，旧测试的 AgentState 加载路径仍工作（保留 `_state_store` 分支）

## 4. execute() 方法——保存路径

- [x] 4.1 在 `execute()` 的保存逻辑中（line 310-313 和 392-393 附近），当 `self._checkpoint_store is not None` 时构造 `SessionState(channel_state=self._channel_manager.snapshot(), agent_state=agent_state.model_dump(mode="json"), event_position=event_position, metadata=metadata)` 并调用 `self._checkpoint_store.save(org_id, user_id, session_id, state)`
- [x] 4.2 在 `_state_store.save(...)` 调用前增加判断：仅当 `self._checkpoint_store is None` 时执行旧的 `_state_store.save`（保持向后兼容）
- [x] 4.3 验证：当 `checkpoint_store` 不为 None 时，**不**调用 `_state_store.save`（避免双写）

## 5. main.py lifespan 初始化 singleton

- [x] 5.1 在 `src/hecate/main.py` 的 lifespan 函数中（plugin discovery 之后），导入 `hecate.services.session_state` 的 `create_session_state_store`
- [x] 5.2 增加 `app.state.session_state_store = create_session_state_store(settings)`（在已有 `SessionStateStore backend=...` log 行之后）
- [x] 5.3 验证：单 worker 启动后，lifespan 中 `app.state.session_state_store` 不为 None

## 6. chat.py DI 注入

- [x] 6.1 在 `src/hecate/api/v1/chat.py` 增加 `from typing import Annotated`、`from fastapi import Depends`、导入 `hecate.core.deps_state_store.get_session_state_store`、`hecate.engine.session_state.SessionStateStore`
- [x] 6.2 修改 chat completion 端点函数签名：增加 `checkpoint_store: Annotated[SessionStateStore, Depends(get_session_state_store)]` 参数
- [x] 6.3 修改 line 214 的 `WorkflowExecutionService(...)` 构造：`exec_service = WorkflowExecutionService(port=port, db=db, checkpoint_store=checkpoint_store)`
- [x] 6.4 验证：chat 端点不传 `state_store`（确认 `_state_store is None`）

## 7. 单元测试——wiring

- [x] 7.1 在 `tests/test_services/test_workflow/test_execution_service_wiring.py` 创建新测试文件（不修改 `test_execution_service.py`）
- [x] 7.2 测试 `WorkflowExecutionService.__init__` 接受 `checkpoint_store` 参数并存储
- [x] 7.3 测试 `__init__` 不传 `checkpoint_store` 时默认为 None（向后兼容）
- [x] 7.4 测试 execute() 调用时 `_checkpoint_store.save` 被调用（用 mock `SessionStateStore`）
- [x] 7.5 测试 execute() 加载时，`_checkpoint_store.load` 被调用并 `AgentState.model_validate` 重建类型化对象
- [x] 7.6 测试 `AgentState.model_validate` 失败时（dict 损坏）fallback 到新建 `AgentState` 并 log warning
- [x] 7.7 测试当 `_checkpoint_store is not None` 时 `_state_store.save` **不被**调用（无双写）

## 8. 单元测试——get_session_state_store DI

- [x] 8.1 在 `tests/test_core/test_deps_state_store.py` 创建新测试文件
- [x] 8.2 测试当 `app.state.session_state_store` 设置时，`get_session_state_store` 返回该实例
- [x] 8.3 测试当 `app.state.session_state_store` 未设置时（模拟测试场景），`get_session_state_store` fallback 到 `create_session_state_store(settings)`
- [x] 8.4 测试 fallback 路径在 `SESSION_STATE_STORE_BACKEND=memory` 时返回 `InMemorySessionStateStore`
- [x] 8.5 测试 chat.py 的 `WorkflowExecutionService` 构造时 `checkpoint_store` 被传入
- [x] 8.6 测试 chat.py **不**传 `state_store` 给 `WorkflowExecutionService`

## 9. 集成测试——端到端 wiring

- [x] 9.1 在 `tests/test_api/test_chat_session_state_wiring.py` 创建端到端测试
- [x] 9.2 测试 chat 完成请求后，`app.state.session_state_store` 中能读取到 session state（memory backend）
- [x] 9.3 测试两次连续 chat 请求（同一 session_id），第二次能恢复第一次的对话历史（验证 wiring 端到端）
- [x] 9.4 测试 `tests/test_services/test_workflow/test_execution_service.py` 中现有 23 个测试全部 pass（向后兼容）

## 10. 验证

- [x] 10.1 跑 `ruff check src/hecate/services/workflow/execution_service.py src/hecate/api/v1/chat.py src/hecate/main.py src/hecate/core/deps_state_store.py tests/test_services/test_workflow/test_execution_service_wiring.py tests/test_core/test_deps_state_store.py tests/test_api/test_chat_session_state_wiring.py` — All checks passed
- [x] 10.2 跑 `ruff format --check` 上述路径 — formatted
- [x] 10.3 跑 `mypy src/hecate/services/workflow/execution_service.py src/hecate/api/v1/chat.py src/hecate/main.py src/hecate/core/deps_state_store.py` — Success: no issues found
- [x] 10.4 跑 `python -m pytest tests/test_services/test_workflow/ tests/test_core/test_deps_state_store.py tests/test_api/test_chat_session_state_wiring.py -v` — 新测试 + 23 现有测试全过
- [x] 10.5 跑完整 `python -m pytest tests/ -q` — 不破坏现有测试
- [x] 10.6 跑完整 `ruff check src/hecate/ tests/` — All checks passed