## Why — 动机

PregelRuntime 当前顺序调度 worker——在任何给定时间只执行一个 worker。对于可以并行运行的分支（由优化通道标记），以及在可能异步执行的任务中，顺序调度顺序会留下性能机会。

## What Changes — 变更内容

- 在 `engine/scheduler.py` 中添加 `SchedulerStrategy` ABC，包含 `select_next(ready: list[WorkerNode], context: dict) -> WorkerNode` 方法
- 添加 `FIFOScheduler`（当前默认行为——先进先出）
- 添加 `PriorityScheduler`（支持在频道可用时基于权重的调度）
- 将 SchedulerStrategy 注册为 PregelRuntime 的可选参数

## Capabilities — 能力变更

### 新增能力
- `scheduler-strategy`: 用于 worker 调度顺序的可插拔策略

### 修改的能力
- 无

## Impact — 影响范围

- **新文件**: `src/hecate/engine/scheduler.py`（ABC + FIFO + Priority 实现）
- **修改的文件**: PregelRuntime 或 worker dispatcher（添加可选的调度器参数）
- **新测试**: `tests/test_engine/test_scheduler.py`
- **无破坏性变更**
- **无新依赖**