## 1. PregelRuntime EventStore 集成

- [x] 1.1 在 `src/hecate/engine/pregel.py` 中添加从 `hecate.engine.eventstore` 导入 `EventStore`、`EventType`、`Event`
- [x] 1.2 在 `PregelRuntime.__init__()` 中添加 `event_store: EventStore | None = None` 参数 — 存储为 `self._event_store = event_store`
- [x] 1.3 添加私有 `_emit()` 辅助方法到 PregelRuntime，在追加前检查 `if self._event_store`
- [x] 1.4 在提供 resume_value 时，在 `_restore_from_checkpoint()` 后记录 RESUME 事件
- [x] 1.5 在全新开始时，在 initial_input 写入后记录 CUSTOM SESSION_START 事件
- [x] 1.6 在分发每个节点前记录 NODE_START 事件
- [x] 1.7 在 worker 返回结果后记录 NODE_END 事件
- [x] 1.8 在抛出 worker 错误前记录 ERROR 事件
- [x] 1.9 在检测到中断时记录 INTERRUPT 事件
- [x] 1.10 在 `_apply_writes()` 后记录 CHANNEL_WRITE 事件
- [x] 1.11 在 checkpoint 保存后记录 CUSTOM SUPERSTEP_END 事件

## 2. Worker ABC 变更

- [x] 2.1 在 `src/hecate/engine/worker.py` 的 `Worker.__init__()` 中添加 `event_store: EventStore | None = None` 参数
- [x] 2.2 在 Worker.execute() 抽象方法中添加 `execution_context: dict | None = None` 参数
- [x] 2.3 在 `src/hecate/engine/worker.py` 的 `WorkerPool.dispatch()` 中添加 `execution_context: dict | None = None` 参数
- [x] 2.4 更新 `DirectWorkerPool.dispatch()` 将 execution_context 传递给 worker.execute()
- [x] 2.5 更新 PregelRuntime，在分发 worker 时传递包含 session_id、superstep、event_store 的 execution_context

## 3. 生产 Worker 更新

- [x] 3.1 更新 `LLMWorker.__init__()` 接受并存储 `event_store` 参数
- [x] 3.2 更新 `LLMWorker.execute()` 接受 `execution_context` 并记录 LLM_REQUEST/LLM_RESPONSE 事件
- [x] 3.3 更新 `ToolWorker.__init__()` 接受并存储 `event_store` 参数
- [x] 3.4 更新 `ToolWorker.execute()` 接受 `execution_context` 并记录 TOOL_CALL/TOOL_RESULT 事件
- [x] 3.5 更新 `AgentWorker.__init__()` 接受 `event_store` 参数
- [x] 3.6 更新 `ConditionWorker`、`KnowledgeWorker`、`VariableSetWorker`、`SuggestionWorker` 构造函数接受 `event_store`

## 4. 测试桩更新

- [x] 4.1 更新 `tests/test_engine/` 中所有 17 个测试 Worker 类，在构造函数中接受 `event_store` 参数

## 5. 测试

- [x] 5.1 在 `tests/test_engine/test_pregel.py` 中添加测试 `test_pregel_records_lifecycle_events` — 验证记录了 NODE_START、NODE_END、CHANNEL_WRITE、SUPERSTEP_END 事件
- [x] 5.2 添加测试 `test_pregel_records_resume_event` — 验证 resume_value 时的 RESUME 事件
- [x] 5.3 添加测试 `test_pregel_records_interrupt_event` — 验证 worker 返回中断命令时的 INTERRUPT 事件
- [x] 5.4 添加测试 `test_pregel_records_error_event` — 验证 worker 返回错误时的 ERROR 事件
- [x] 5.5 添加测试 `test_pregel_no_recording_without_event_store` — 验证 event_store 为 None 时没有记录事件

## 6. 验证

- [x] 6.1 运行 `ruff check src/hecate/ tests/`
- [x] 6.2 运行 `ruff format --check src/ tests/`
- [x] 6.3 运行 `mypy src/`
- [x] 6.4 运行 `python -m pytest tests/ -q` — 无回归
