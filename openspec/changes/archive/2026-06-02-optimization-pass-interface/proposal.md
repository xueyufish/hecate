## Why — 动机

图编译器目前在一个计划步骤中编译整个图。对于包含数十个节点和多种并行分支的大图，这产生了任何优化都难以触及的单一执行计划。OptimizationPass 接口允许将优化步骤注入到不同阶段：编译后的计划可以在一次执行和下一次执行之间通过死节点消除或并行分支检测等方式进行优化。

## What Changes — 变更内容

- 在 `engine/optimization.py` 中添加 `OptimizationPass` ABC，包含 `optimize(plan: GraphPlan) -> GraphPlan` 方法
- 添加 `DeadNodeElimination`（移除没有入边且不是入口的节点）
- 添加 `ParallelBranchDetection`（识别可以并发运行的分支）
- 将 OptimizationPass 注册为 PregelRuntime 或编译器调用者的可选参数

## Capabilities — 能力变更

### 新增能力
- `optimization-pass`: 每次执行后对图计划运行可插拔的优化

### 修改的能力
- 无

## Impact — 影响范围

- **新文件**: `src/hecate/engine/optimization.py`（ABC + 实现）
- **修改的文件**: 编译器或 PregelRuntime（添加可选的优化参数）
- **新测试**: `tests/test_engine/test_optimization.py`
- **无破坏性变更**
- **无新依赖**