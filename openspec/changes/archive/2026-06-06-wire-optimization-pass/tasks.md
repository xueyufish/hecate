## 1. GraphCompiler Constructor

- [x] 1.1 Add `__init__(self, passes: list[OptimizationPass] | None = None) -> None` to `GraphCompiler` in `src/hecate/engine/compiler.py` — store `self._passes = passes or []`
- [x] 1.2 Add import for `OptimizationPass` from `hecate.engine.optimization` in `src/hecate/engine/compiler.py`

## 2. Apply Optimization Passes

- [x] 2.1 In `GraphCompiler.compile()`, after constructing `CompiledGraph`, iterate `self._passes` and apply each `optimize()` sequentially before returning — `for p in self._passes: graph = p.optimize(graph)`

## 3. Tests

- [x] 3.1 Add test `test_compiler_default_no_optimization` in `tests/test_engine/test_graph_dsl.py` — compile a graph with unreachable nodes using `GraphCompiler()` (no passes), verify unreachable nodes are still present (current behavior preserved)
- [x] 3.2 Add test `test_compiler_single_pass` in `tests/test_engine/test_graph_dsl.py` — compile with `passes=[DeadNodeElimination()]`, verify unreachable nodes are removed
- [x] 3.3 Add test `test_compiler_multi_pass_pipeline` in `tests/test_engine/test_graph_dsl.py` — compile with `passes=[DeadNodeElimination(), ParallelBranchDetection()]`, verify both passes are applied in order

## 4. Verification

- [x] 4.1 Run `ruff check src/hecate/ tests/`
- [x] 4.2 Run `ruff format --check src/ tests/`
- [x] 4.3 Run `mypy src/`
- [x] 4.4 Run `python -m pytest tests/ -q` — no regressions
