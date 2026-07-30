## 1. OptimizationPass ABC — OptimizationPass ABC

- [x] 1.1 创建 `src/hecate/engine/optimization.py`，包含定义 `optimize(plan: GraphPlan) -> GraphPlan` 的 `OptimizationPass(ABC)`
- [x] 1.2 为 ABC 和方法添加完整的文档字符串

## 2. DeadNodeElimination — DeadNodeElimination

- [x] 2.1 实现 `DeadNodeElimination(OptimizationPass)`，移除没有入边且不是入口节点的节点
- [x] 2.2 添加文档字符串解释正确性保证
- [x] 2.3 确保不会将入口节点排除在外

## 3. ParallelBranchDetection — ParallelBranchDetection

- [x] 3.1 实现 `ParallelBranchDetection(OptimizationPass)`，识别独立的分支（共享共同的父节点但彼此之间没有路径）
- [x] 3.2 标记并行区域，不改变计划结构

## 4. Tests — 测试

- [x] 4.1 创建 `tests/test_engine/test_optimization.py`
- [x] 4.2 测试 OptimizationPass ABC 不可实例化
- [x] 4.3 测试 DeadNodeElimination 移除去往不可达节点的边
- [x] 4.4 测试 DeadNodeElimination 保留合法图结构
- [x] 4.5 测试 ParallelBranchDetection 正确标记独立的并行分支
- [x] 4.6 测试 Passes 是可组合的（按顺序应用）

## 5. Verification — 验证

- [x] 5.1 运行 `ruff check src/hecate/engine/optimization.py tests/test_engine/test_optimization.py`
- [x] 5.2 运行 `ruff format --check src/hecate/engine/optimization.py tests/test_engine/test_optimization.py`
- [x] 5.3 运行 `mypy src/hecate/engine/optimization.py`
- [x] 5.4 运行 `python -m pytest tests/test_engine/test_optimization.py -v`
- [x] 5.5 运行完整测试套件 `python -m pytest tests/ -q`