## ADDED Requirements — 新增需求

### Requirement：OptimizationPass ABC 定义可插拔的计划优化 — OptimizationPass ABC 定义可插拔的计划优化
引擎 SHALL 在 `engine/optimization.py` 中定义一个 `OptimizationPass` ABC，包含接受并返回 `GraphPlan` 的 `optimize(plan: GraphPlan) -> GraphPlan` 方法。

#### Scenario：优化图计划
- **WHEN** 调用 `pass.optimize(plan)`
- **THEN** 它 SHALL 返回一个新的 `GraphPlan` 实例（或原地修改后返回相同的计划）

### Requirement：DeadNodeElimination 移除不可达节点 — DeadNodeElimination 移除不可达节点
`DeadNodeElimination` SHALL 移除任何没有入边的节点（入口节点除外）。这避免了为无法被触发的节点分配资源。

#### Scenario：移除去往不可达节点的边
- **WHEN** 一个图包含入口节点 A → B → C 和 C → E，其中 D 是引用 E 的无人到达的节点
- **THEN** 优化 SHALL 移除 D（没有入边且不是入口节点）

#### Scenario：保留入口节点
- **WHEN** 入口节点没有入边
- **THEN** 它 SHALL 保留在计划中

### Requirement：ParallelBranchDetection 标记并行区域 — ParallelBranchDetection 标记并行区域
`ParallelBranchDetection` SHALL 识别可以安全并发执行的分支（共享共同的父节点但彼此之间没有路径）。它 SHALL 标记这些区域而不改变计划结构。

#### Scenario：识别并行分支
- **WHEN** 一个图包含 A → B、A → C（B 和 C 之间无路径）
- **THEN** B 和 C SHALL 被标记为可并行运行

#### Scenario：不标记串行分支
- **WHEN** 一个图包含 A → B → C
- **THEN** SHALL 不标记任何区域为并行