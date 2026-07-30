## Context — 背景

GraphCompiler 目前是一个无状态类——没有构造函数，只有一个 `compile()` 方法。它验证 GraphConfig 并返回一个 CompiledGraph。engine 定义了 `OptimizationPass` ABC 及其两个实现（`DeadNodeElimination`、`ParallelBranchDetection`），但它们从未被调用。编译器的 `_detect_unreachable()` 只记录警告而不采取行动。

三个 services 层的调用者通过无参方式实例化 `GraphCompiler()`：`workflow_service.py`、`execution_service.py` 和 `test_runner.py`。十六个测试文件也内联创建了 `GraphCompiler()` 实例。

## Goals / Non-Goals — 目标 / 非目标

**目标：**
- 通过构造函数注入将 OptimizationPass 接入 GraphCompiler
- 在验证之后、返回 CompiledGraph 之前应用优化 passes
- 保持完全向后兼容（无参构造函数 = 当前行为）
- 遵循现有的 ABC 接入模式（SchedulerStrategy → PregelRuntime、EvictionPolicy → ChannelManager）

**非目标：**
- 向 CompiledGraph 添加 `metadata` 字段（ParallelBranchDetection 写入它，但这是预先存在的问题）
- 将 `_detect_unreachable()` 重构为 DeadNodeElimination（目的不同——警告 vs 消除）
- 更改 services 层代码以注入 passes（services 目前使用默认值）
- 实现新的优化 passes

## Decisions — 设计决策

### D1: passes 是一个列表，而不是单个 OptimizationPass

**选择**：`passes: list[OptimizationPass] | None = None`

**理由**：现有的规范（`openspec/specs/optimization-pass/spec.md`）规定了一个列表参数。多个 passes 形成一个流水线——例如，首先消除死节点，然后在清理后的图中检测并行分支。单 pass API 需要为此常见情况进行组合。

**替代方案**：单个 `optimization: OptimizationPass | None = None`（匹配 SchedulerStrategy 模式）。被否决——与规范不匹配，灵活性较差。

### D2: 默认是空列表（不优化）

**选择**：`self._passes = passes or []`

**理由**：当前行为是无优化。空列表 = 恒等变换。不需要无操作默认类（不像 `NoEviction`/`FIFOScheduler`）。

### D3: Passes 在 CompiledGraph 构建之后运行

**选择**：先构建 CompiledGraph，然后按顺序应用 passes。

```python
graph = CompiledGraph(nodes=config.nodes, edges=config.edges, ...)
for p in self._passes:
    graph = p.optimize(graph)
return graph
```

**理由**：OptimizationPass 操作的是 `CompiledGraph`，而不是 `GraphConfig`。验证流水线确保图在结构上是健全的，然后任何优化才能触及它。这与 OptimizationPass ABC 契约匹配：`optimize(graph: CompiledGraph) -> CompiledGraph`。

### D4: 保留 `_detect_unreachable` 与 DeadNodeElimination 并存

**理由**：`_detect_unreachable()` 在开发期间记录警告（即使没有优化 passes 也很有用）。`DeadNodeElimination` 实际移除节点。目的不同，两者都有价值。BFS 重复是可以接受的——它们操作的是不同类型（`GraphConfig` vs `CompiledGraph`）。

## Risks / Trade-offs — 风险与权衡

- **接入 DeadNodeElimination 时 BFS 运行两次** → 可接受的开销；编译器对每个图定义运行一次，而不是每个执行步骤
- **Pass 排序是调用者的责任** → 在文档字符串中记录 passes 按列表顺序执行
- **ParallelBranchDetection 写入不存在的 `metadata` 字段** → 预先存在的错误，不是此更改引入的；不在范围内
