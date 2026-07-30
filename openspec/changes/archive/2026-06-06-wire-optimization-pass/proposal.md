## Why — 动机

OptimizationPass ABC 及其两个实现（DeadNodeElimination、ParallelBranchDetection）存在于 `engine/optimization.py` 中，但从未在编译期间被调用。GraphCompiler 在验证后生成 CompiledGraph，但完全跳过了优化阶段。这使得图中存在不可达的节点并在运行时会错过并行分支检测的机会。将 OptimizationPass 接入 GraphCompiler 完成了 Sprint 1 架构加固的目标——连接所有已定义的 engine ABC。

## What Changes — 变更内容

- GraphCompiler 获得一个 `__init__` 方法，接受可选的 `passes` 参数（OptimizationPass 列表，默认为空）
- 在验证和 CompiledGraph 构建之后，每个 pass 通过 `optimize()` 按顺序应用
- 所有现有的 `GraphCompiler()` 实例化保持向后兼容（无参构造函数仍然有效）

## Capabilities — 能力变更

### 新增能力

（无）

### 修改的能力

- `optimization-pass`: GraphCompiler SHALL 接受可选的优化 passes 并在验证后应用它们

## Impact — 影响范围

- `src/hecate/engine/compiler.py` — 添加构造函数，在 `compile()` 中应用 passes
- `tests/test_engine/test_graph_dsl.py` — 添加优化 pass 集成测试
- Services（`workflow_service.py`、`execution_service.py`、`test_runner.py`）— 无需更改（向后兼容）
- `src/hecate/engine/optimization.py` — 无需更改（ABC + 实现已存在）
