## 1. SchedulerStrategy ABC — SchedulerStrategy ABC

- [x] 1.1 创建 `src/hecate/engine/scheduler.py`，包含定义 `select_next(ready: list[WorkerNode], context: dict) -> WorkerNode` 的 `SchedulerStrategy(ABC)`
- [x] 1.2 为 ABC 和方法添加完整的文档字符串

## 2. FIFOScheduler Implementation — FIFOScheduler 实现

- [x] 2.1 实现 `FIFOScheduler(SchedulerStrategy)`，返回 `ready` 列表中的第一个节点
- [x] 2.2 添加文档字符串

## 3. PriorityScheduler Implementation — PriorityScheduler 实现

- [x] 3.1 实现 `PriorityScheduler(SchedulerStrategy)`，允许为节点设置权重
- [x] 3.2 实现 `set_weights(node_weights: dict[str, int])` 方法
- [x] 3.3 `select_next` 返回就绪节点中权重最高的节点
- [x] 3.4 默认将未显式设置权重的节点的权重视为 1

## 4. Tests — 测试

- [x] 4.1 创建 `tests/test_engine/test_scheduler.py`
- [x] 4.2 测试 SchedulerStrategy ABC 不可实例化
- [x] 4.3 测试 FIFOScheduler 返回第一个就绪节点
- [x] 4.4 测试 PriorityScheduler 返回权重最高的节点
- [x] 4.5 测试 PriorityScheduler 对未设置权重的节点使用默认值 1

## 5. Verification — 验证

- [x] 5.1 运行 `ruff check src/hecate/engine/scheduler.py tests/test_engine/test_scheduler.py`
- [x] 5.2 运行 `ruff format --check src/hecate/engine/scheduler.py tests/test_engine/test_scheduler.py`
- [x] 5.3 运行 `mypy src/hecate/engine/scheduler.py`
- [x] 5.4 运行 `python -m pytest tests/test_engine/test_scheduler.py -v`
- [x] 5.5 运行完整测试套件 `python -m pytest tests/ -q`