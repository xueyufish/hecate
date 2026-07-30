## MODIFIED Requirements — 修改的需求

### Requirement: PregelRuntime 接受可选的调度器 — PregelRuntime accepts optional scheduler
PregelRuntime 应在其构造函数中接受可选的 `scheduler` 参数，默认使用 `FIFOScheduler()`。

#### Scenario: 默认调度器 — Default scheduler
- **当** 创建 PregelRuntime 时不带 scheduler 参数
- **则** 它应在内部使用 `FIFOScheduler()`

#### Scenario: 自定义调度器 — Custom scheduler
- **当** 使用自定义调度器创建 PregelRuntime
- **则** 它应在每个超步中使用该调度器进行节点排序

#### Scenario: 显式 None — Explicit None
- **当** 使用 `scheduler=None` 创建 PregelRuntime
- **则** 它应在内部使用 `FIFOScheduler()`（与省略参数相同）

## ADDED Requirements — 新增需求

### Requirement: PregelRuntime 在节点调度前调用调度器 — PregelRuntime calls scheduler before node dispatch
在每个超步开始时，计算 `current_nodes` 之后，PregelRuntime 应调用 `self._scheduler.select_next(current_nodes, context)` 并迭代返回的顺序而非原始的 `current_nodes`。

#### Scenario: 每超步调用调度器 — Scheduler called every superstep
- **当** 一个超步以 `current_nodes = ["node_b", "node_a"]` 开始
- **则** `scheduler.select_next(["node_b", "node_a"], context)` 应被精确调用一次
- **并且** 运行时应按返回的顺序迭代节点

#### Scenario: 调度器上下文包含超步和快照 — Scheduler context includes superstep and snapshot
- **当** 在超步 3 期间调用 `select_next`，通道快照为 `{"messages": [...]}`
- **则** `context` 字典应包含 `"superstep": 3` 和 `"channel_snapshot": {"messages": [...]}`

#### Scenario: 单节点超步 — Single-node superstep
- **当** 一个超步只有一个节点（`current_nodes = ["only_node"]`）
- **则** `select_next` 仍应以 `["only_node"]` 被调用
- **并且** 结果应被用于迭代（即使顺序是平凡的）

#### Scenario: 空 current_nodes — Empty current_nodes
- **当** 一个超步没有节点（`current_nodes = []`）
- **则** `select_next` 不应被调用（while 循环条件阻止进入超步体）

### Requirement: 调度器透明接收 FAN_OUT 和 MERGE 节点 — Scheduler receives FAN_OUT and MERGE nodes transparently
调度器应接收 `current_nodes` 中的所有节点 ID，包括 FAN_OUT 和 MERGE 类型的节点。这些节点类型的特殊处理发生在调度之后的调度循环内。

#### Scenario: current_nodes 中的 FAN_OUT 节点 — FAN_OUT node in current_nodes
- **当** `current_nodes` 包含一个 FAN_OUT 节点 ID
- **则** `select_next` 应像其他节点一样接收它
- **并且** 循环体应照常通过 `_dispatch_fan_out()` 调度它

#### Scenario: current_nodes 中的 MERGE 节点 — MERGE node in current_nodes
- **当** `current_nodes` 包含一个 MERGE 节点 ID
- **则** `select_next` 应像其他节点一样接收它
- **并且** 循环体应照常通过 `_execute_merge()` 执行它
