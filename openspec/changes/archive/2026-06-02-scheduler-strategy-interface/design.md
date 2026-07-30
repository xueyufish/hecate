## Context — 背景

Worker 按照它们被添加到图中的顺序执行。对于更动态的调度——例如并行运行时、带权重的 DAG 中的优先级分配——需要一个允许不同调度策略的灵活接口。

## Goals / Non-Goals — 目标 / 非目标

**目标：**
- 定义具有 `select_next(ready: list[WorkerNode], context: dict) -> WorkerNode` 的 `SchedulerStrategy` ABC
- 提供 `FIFOScheduler`（与当前行为匹配）和 `PriorityScheduler`
- 使调度策略成为 PregelRuntime 的可选依赖
- 保持引擎零依赖

**非目标：**
- 用于离线分析的可序列化调度（P3）
- 动态重新平衡（P3+）

## Decisions — 设计决策

### D1：SchedulerStrategy 在引擎内部

**选择**：创建 `engine/scheduler.py`，与 `engine/pregel.py` 并列。

**理由**：调度是执行细节，不是服务边界。

### D2：FIFO 作为默认调度

**选择**：`FIFOScheduler` 通过始终选择 `ready` 列表中的第一个节点来镜像当前的顺序行为。

**理由**：立即兼容现有的 PregelRuntime 实现。

### D3：PriorityScheduler 使用频道状态

**选择**：`PriorityScheduler` 在上下文中检查频道可用性，并偏好等待关键输入的那些就绪节点。

**理由**：无论可用状态如何都执行节点可以是次优的。基于权重的调度提供了更好的并行化。

## Risks / Trade-offs — 风险与权衡

| 风险 | 缓解措施 |
|------|---------|
| 优先级调度可能使图执行复杂化 | 提供为简单案例模拟现有行为的 FIFO 默认值 |
| 调度器改变性能特征 | 调度器是可选的；不设置时默认使用 FIFO |