## ADDED Requirements — 新增需求

### Requirement：SchedulerStrategy ABC 定义可插拔的 worker 调度 — SchedulerStrategy ABC 定义可插拔的 worker 调度
引擎 SHALL 在 `engine/scheduler.py` 中定义一个 `SchedulerStrategy` ABC，包含 `select_next(ready: list[WorkerNode], context: dict) -> WorkerNode` 方法。

#### Scenario：选择下一个 worker
- **WHEN** 使用就绪 worker 列表和上下文字典调用 `select_next`
- **THEN** 它 SHALL 返回要执行的下一个 `WorkerNode`

### Requirement：FIFOScheduler 以先进先出顺序调度 — FIFOScheduler 以先进先出顺序调度
`FIFOScheduler` SHALL 返回 `ready` 列表中的第一个 worker 节点，与当前顺序行为匹配。

#### Scenario：返回第一个就绪节点
- **WHEN** 就绪列表是 `[A, B, C]`
- **THEN** `select_next` SHALL 返回 `A`

### Requirement：PriorityScheduler 按权重调度 — PriorityScheduler 按权重调度
`PriorityScheduler` SHALL 从就绪列表中选择权重最高的节点。权重通过 `set_weights(node_weights: dict[str, int])` 分配。未显式设置权重的节点默认权重为 1。

#### Scenario：选择权重最高的节点
- **WHEN** A 权重为 1，B 权重为 10，C 权重为 5，且都就绪
- **THEN** `select_next` SHALL 返回 `B`

#### Scenario：未分配权重的默认值
- **WHEN** A 没有分配权重
- **THEN** A SHALL 在 select_next 中权重为 1