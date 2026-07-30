## Context — 背景

EventStore ABC 已存在，具有只追加的事件持久化契约。InMemoryEventStore 提供测试实现。EventType 枚举定义了 11 个事件类别。PregelRuntime 在超级步循环中执行编译后的图，但没有事件记录能力。Worker ABC 没有构造函数，也没有事件记录机制。存在 8 个生产 worker 和 17 个测试桩。

路线图将 EventStore 接入列为 Sprint 1 架构加固任务（约 20 行）。Sprint 4 功能（全链路追踪、审计日志）依赖于此。

## Goals / Non-Goals — 目标 / 非目标

**目标：**
- 将 EventStore 接入 PregelRuntime 以记录节点生命周期事件（Plan A）
- 将 EventStore 接入 Worker 以记录执行细节事件（Plan B）
- 采用构造函数注入模式（与 SchedulerStrategy、EvictionPolicy 一致）
- 保持完全向后兼容（无 event_store = 无记录）
- 启用未来的可观测性功能，无需进一步的接口更改

**非目标：**
- 实现 PostgresEventStore 或持久化存储（InMemoryEventStore 目前足够）
- 添加新的 EventType 值（CUSTOM 处理扩展）
- 事件过滤或采样（推迟到 Sprint 4）
- 更改 Worker.execute() 抽象方法签名

## Decisions — 设计决策

### D1: PregelRuntime 和 Worker 的构造函数注入

**选择**：两个构造函数中均使用 `event_store: EventStore | None = None`。

**理由**：与现有的 ABC 接入模式一致。可选参数保持向后兼容。

### D2: Worker 通过 execute() 参数接收执行上下文

**选择**：将 `execution_context: dict | None = None` 添加到 Worker.execute()。PregelRuntime 填充为 `{"session_id": UUID, "superstep": int, "event_store": EventStore}`。

**理由**：Worker 需要 session_id 和 superstep 来创建 Events。这些是每次超级步都会变化的 PregelRuntime 级状态。将它们存储为 Worker 状态需要 PregelRuntime 在每次调度前更新 Worker。在调用时传递的上下文字典更清晰，避免了可变的 Worker 状态。

**替代方案**：在 Worker 构造函数中存储 session_id。被否决——superstep 每次迭代都会变化，因此仅构造函数注入不够。

### D3: Worker ABC 构造函数接受 event_store 但不要求它

**选择**：Worker ABC 新增 `def __init__(self, event_store: EventStore | None = None)`。覆盖 __init__ 的子类应接受并传递 event_store。

**理由**：这使 Worker 子类可以访问 event_store 以记录 B 类事件。不覆盖 __init__ 的测试桩继承默认值（None）并保持不变。

### D4: PregelRuntime 中的 _emit() 辅助方法

**选择**：私有方法 `_emit(session_id, event_type, node_id=None, payload=None)`，在追加前检查 `if self._event_store`。

**理由**：减少样板代码——每个事件记录点变成一行而不是五行。

### D5: PregelRuntime 中的事件记录点

| 事件类型 | 触发时机 | 载荷 |
|---------|---------|------|
| CUSTOM (SESSION_START) | 全新执行开始 | `{"event_name": "SESSION_START", "initial_input_keys": [...]}` |
| RESUME | 从 checkpoint 恢复后 | `{"interrupted_node": str}` |
| NODE_START | 分发 worker 之前 | `{"node_type": str}` |
| NODE_END | worker 返回后 | `{"success": bool, "has_command": bool}` |
| ERROR | 抛出错误前 | `{"error_type": str, "error_message": str}` |
| INTERRUPT | 检测到中断时 | `{"interrupt_value_type": str}` |
| CHANNEL_WRITE | _apply_writes 之后 | `{"channels": list[str]}` |
| CUSTOM (SUPERSTEP_END) | checkpoint 保存后 | `{"event_name": "SUPERSTEP_END", "completed_nodes": int}` |

### D6: Worker 中的事件记录点（仅生产 worker）

| 事件类型 | 触发时机 | Worker |
|---------|---------|--------|
| LLM_REQUEST | LLM 调用前 | LLMWorker |
| LLM_RESPONSE | LLM 响应后 | LLMWorker |
| TOOL_CALL | 工具调用前 | ToolWorker |
| TOOL_RESULT | 工具结果后 | ToolWorker |

测试桩忽略 execution_context 并且不发出事件。

## Risks / Trade-offs — 风险与权衡

- **涉及 25 个文件**（8 个生产 worker + 17 个测试桩需要构造函数变更）→ 大多数变更是机械性的（添加 `event_store=None` 参数）
- **性能开销** → EventStore.append() 是异步的，在异步上下文中调用。对于 InMemoryEventStore 开销可忽略。生产部署可以省略 event_store 以完全避免开销。
- **execution_context 字典是无类型的** → 目前可以接受；以后如果需要可以细化为数据类
- **测试桩需要更新** → 所有 17 个测试 Worker 类需要在构造函数中接受 event_store。机械性变更，对逻辑无影响。
