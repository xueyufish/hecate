## 1. GraphCompiler 构造函数

- [x] 1.1 在 `src/hecate/engine/compiler.py` 的 `GraphCompiler` 中添加 `__init__(self, passes: list[OptimizationPass] | None = None) -> None` — 存储 `self._passes = passes or []`
- [x] 1.2 在 `src/hecate/engine/compiler.py` 中添加从 `hecate.engine.optimization` 导入 `OptimizationPass`

## 2. 应用优化 Passes

- [x] 2.1 在 `GraphCompiler.compile()` 中，构造 `CompiledGraph` 后，遍历 `self._passes` 并在返回前依次应用每个 `optimize()` — `for p in self._passes: graph = p.optimize(graph)`

## 3. 测试

- [x] 3.1 在 `tests/test_engine/test_graph_dsl.py` 中添加测试 `test_compiler_default_no_optimization` — 使用 `GraphCompiler()`（无 passes）编译带有不可达节点的图，验证不可达节点仍然存在（当前行为保持不变）
- [x] 3.2 在 `tests/test_engine/test_graph_dsl.py` 中添加测试 `test_compiler_single_pass` — 使用 `passes=[DeadNodeElimination()]` 编译，验证不可达节点被移除
- [x] 3.3 在 `tests/test_engine/test_graph_dsl.py` 中添加测试 `test_compiler_multi_pass_pipeline` — 使用 `passes=[DeadNodeElimination(), ParallelBranchDetection()]` 编译，验证两个 passes 按顺序应用

## 4. 验证

- [x] 4.1 运行 `ruff check src/hecate/ tests/`
- [x] 4.2 运行 `ruff format --check src/ tests/`
- [x] 4.3 运行 `mypy src/`
- [x] 4.4 运行 `python -m pytest tests/ -q` — 无回归
