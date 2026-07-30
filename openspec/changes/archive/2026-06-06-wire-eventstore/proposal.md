## Why — 动机

EventStore ABC 和 InMemoryEventStore 存在于 `engine/eventstore.py` 中，但从未在图执行期间被调用。PregelRuntime 无法发出执行事件，Workers 无法记录内部细节（LLM 调用、工具调用）。这阻碍了审计日志、时间旅行调试和可观测性路径（Sprint 4 的 feature 8.1 全链路追踪、8.7 审计日志）。

## What Changes — 变更内容

**PregelRuntime 层（Plan A）：**
- 构造函数接受可选的 `event_store: EventStore | None = None`
- 在执行期间记录生命周期事件：NODE_START、NODE_END、INTERRUPT、RESUME、ERROR、CHANNEL_WRITE、SUPERSTEP_END
- 使用私有 `_emit()` 辅助方法减少样板代码

**Worker 层（Plan B）：**
- Worker ABC 新增可选的 `event_store` 构造函数参数
- Worker.execute() 新增可选的 `execution_context` 字典，包含 `session_id`、`superstep`、`event_store`
- 生产 Workers（LLMWorker、ToolWorker 等）记录 LLM_REQUEST、LLM_RESPONSE、TOOL_CALL、TOOL_RESULT 事件
- 测试桩保持不变（忽略 execution_context）

## Capabilities — 能力变更

### 新增能力

（无）

### 修改的能力

- `eventstore`: EventStore SHALL 被接入 PregelRuntime 和 Worker，以便在图执行期间记录事件

## Impact — 影响范围

- `src/hecate/engine/pregel.py` — 构造函数 + execute() 事件记录（约 20 行）
- `src/hecate/engine/worker.py` — ABC 新增可选的 event_store 参数
- `src/hecate/engine/workers/*.py` — 8 个生产 worker 接受 event_store
- `tests/test_engine/test_pregel.py` — 事件记录集成测试
- `tests/test_engine/test_eventstore.py` — 与 PregelRuntime 的集成测试
- 向后兼容：所有现有代码在没有 event_store 的情况下继续正常工作
