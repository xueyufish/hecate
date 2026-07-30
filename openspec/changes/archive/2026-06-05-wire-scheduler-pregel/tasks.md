## 1. PregelRuntime Constructor — PregelRuntime 构造函数

- [x] 1.1 在 `src/hecate/engine/pregel.py` 的 `PregelRuntime.__init__` 中添加 `scheduler: SchedulerStrategy | None = None` 参数
- [x] 1.2 在构造函数体中存储为 `self._scheduler = scheduler or FIFOScheduler()`
- [x] 1.3 在 `pregel.py` 顶部添加 `from hecate.engine.scheduler import FIFOScheduler, SchedulerStrategy` 导入

## 2. Superstep Loop Wiring — 超步循环接线

- [x] 2.1 在 `execute()` 中，在快照行之后构建 `context = {"superstep": self._superstep, "channel_snapshot": snapshot}`
- [x] 2.2 在 `for node_id in current_nodes:` 循环之前插入 `scheduled_nodes = self._scheduler.select_next(current_nodes, context)`
- [x] 2.3 将循环改为迭代 `scheduled_nodes` 而非 `current_nodes`

## 3. Tests — 测试

- [x] 3.1 创建 `tests/test_engine/test_scheduler_integration.py`，包含记录所有 `select_next` 调用的 `TrackingScheduler` 桩
- [x] 3.2 测试：默认调度器（无参数）使用 FIFOScheduler——验证执行与现有行为完全相同
- [x] 3.3 测试：自定义调度器在每个超步被调用——验证 `select_next` 以正确的节点列表和上下文字典被调用
- [x] 3.4 测试：自定义调度器重新排序节点——提供一个反转调度器并验证执行顺序与输入不同
- [x] 3.5 测试：上下文字典包含 `superstep` 和 `channel_snapshot` 键，值正确
- [x] 3.6 测试：单节点超步仍调用 `select_next`（不跳过）
- [x] 3.7 运行 `python -m pytest tests/test_engine/test_scheduler_integration.py -v`——全部通过

## 4. Verification — 验证

- [x] 4.1 运行 `ruff check src/hecate/engine/pregel.py tests/test_engine/test_scheduler_integration.py`
- [x] 4.2 运行 `ruff format --check src/hecate/engine/pregel.py tests/test_engine/test_scheduler_integration.py`
- [x] 4.3 运行 `mypy src/hecate/engine/pregel.py`
- [x] 4.4 运行 `python -m pytest tests/ -q`——无回归
