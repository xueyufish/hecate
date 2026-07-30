## MODIFIED Requirements — 修改的需求

### Requirement: GraphCompiler 接受可选的优化 passes — GraphCompiler 接受可选的优化 passes
GraphCompiler SHALL 在其构造函数中接受可选的 `passes: list[OptimizationPass] | None = None` 参数，默认为空列表。在验证和 CompiledGraph 构建之后，GraphCompiler SHALL 通过 `optimize()` 依次应用每个 pass，将一个 pass 的输出作为下一个 pass 的输入。

#### Scenario: 默认不优化
- **WHEN** GraphCompiler 在未指定 passes 参数的情况下创建
- **THEN** 它 SHALL 在不优化的情况下编译（当前行为——空 pass 列表）

#### Scenario: 单个优化 pass
- **WHEN** GraphCompiler 使用 `passes=[DeadNodeElimination()]` 创建
- **THEN** 它 SHALL 在验证后应用 DeadNodeElimination.optimize() 并返回优化后的 CompiledGraph

#### Scenario: 流水线中的多个优化 passes
- **WHEN** GraphCompiler 使用 `passes=[DeadNodeElimination(), ParallelBranchDetection()]` 创建
- **THEN** 它 SHALL 先应用 DeadNodeElimination.optimize()，然后将结果传递给 ParallelBranchDetection.optimize()

#### Scenario: Pass 排序保持不变
- **WHEN** passes=[P, Q] 其中 P 和 Q 是 OptimizationPass 实现
- **THEN** P.optimize() SHALL 在 Q.optimize() 之前被调用
