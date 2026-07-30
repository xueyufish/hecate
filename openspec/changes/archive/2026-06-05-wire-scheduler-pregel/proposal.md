## Why — 为什么

`SchedulerStrategy` ABC 和 `FIFOScheduler` 已在 `engine/scheduler.py` 中定义，但从未被 `PregelRuntime` 使用。运行时仍然在 `for` 循环中直接迭代 `current_nodes`（pregel.py 第 144 行）。这种脱节意味着已发布的规范（`openspec/specs/scheduler-strategy/spec.md`）承诺的行为（"PregelRuntime 应接受可选的 `scheduler` 参数"）是代码未实现的。将调度器接入运行时关闭了这一规范-实现差距，并为 P2 并行 WorkerPool 调度做好准备。

## What Changes — 变更内容

- 向 `PregelRuntime.__init__` 添加可选的 `scheduler: SchedulerStrategy | None = None` 参数，默认使用 `FIFOScheduler()`
- 在 `execute()` 的 `for node_id in current_nodes:` 循环之前插入 `self._scheduler.select_next(current_nodes, context)` 调用
- 构建包含 `superstep` 和 `channel_snapshot` 键的 `context` 字典
- 更新 `services/workflow/execution_service.py` 和 `services/workflow/test_runner.py`（两个 PregelRuntime 实例化点）以通过默认参数保持兼容
- 添加测试验证调度器在执行期间被调用，且节点顺序可自定义

## Capabilities — 能力

### 新能力

（无——SchedulerStrategy ABC 已存在）

### 修改的能力

- `scheduler-strategy`：扩展已发布的规范，添加关于 PregelRuntime 如何在超步循环中调用调度器的需求（构造函数接线、上下文字典内容、调用点语义）

## Impact — 影响

- **修改的文件**：`src/hecate/engine/pregel.py`——添加调度器参数 + select_next 调用
- **修改的文件**：`openspec/specs/scheduler-strategy/spec.md`——添加接线需求
- **新测试**：`tests/test_engine/test_scheduler_integration.py`——验证调度器在 PregelRuntime.execute() 期间被调用
- **无破坏性变更**：默认 FIFOScheduler 保持相同行为
- **无新依赖**：使用 `engine/scheduler.py` 中现有的 SchedulerStrategy
