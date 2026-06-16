## 1. OptimizationPass ABC

- [x] 1.1 Create `src/hecate/engine/optimization.py` with `OptimizationPass(ABC)` defining abstract method: `optimize(graph: CompiledGraph) -> CompiledGraph`
- [x] 1.2 Add full docstrings

## 2. DeadNodeElimination

- [x] 2.1 Implement `DeadNodeElimination(OptimizationPass)` that removes unreachable nodes
- [x] 2.2 Use BFS from entry point to find reachable nodes
- [x] 2.3 Remove unreachable nodes from nodes dict
- [x] 2.4 Remove edges referencing unreachable nodes
- [x] 2.5 Handle case: no entry point (return graph unchanged)
- [x] 2.6 Add docstrings

## 3. ParallelBranchDetection

- [x] 3.1 Implement `ParallelBranchDetection(OptimizationPass)` that detects parallel branches
- [x] 3.2 Build adjacency list from edges
- [x] 3.3 Find nodes with multiple outgoing edges (branch points)
- [x] 3.4 For each branch point, find independent branches (no shared descendants)
- [x] 3.5 Mark parallel groups in graph metadata
- [x] 3.6 Add docstrings

## 4. GraphCompiler Integration

- [x] 4.1 Add optional `passes: list[OptimizationPass] | None = None` parameter to `GraphCompiler.__init__`
- [x] 4.2 After validation, apply each pass in order
- [x] 4.3 Return the optimized graph

## 5. Tests

- [x] 5.1 Create `tests/test_engine/test_optimization.py`
- [x] 5.2 Test OptimizationPass is abstract
- [x] 5.3 Test DeadNodeElimination removes unreachable nodes
- [x] 5.4 Test DeadNodeElimination preserves reachable nodes
- [x] 5.5 Test DeadNodeElimination with no entry point
- [x] 5.6 Test ParallelBranchDetection marks parallel branches
- [x] 5.7 Test ParallelBranchDetection with linear graph
- [x] 5.8 Test GraphCompiler default has no passes
- [x] 5.9 Test GraphCompiler with passes applies optimization

## 6. Verification

- [x] 6.1 Run `ruff check src/hecate/engine/optimization.py src/hecate/engine/compiler.py tests/test_engine/test_optimization.py`
- [x] 6.2 Run `ruff format --check src/hecate/engine/optimization.py src/hecate/engine/compiler.py tests/test_engine/test_optimization.py`
- [x] 6.3 Run `mypy src/hecate/engine/optimization.py src/hecate/engine/compiler.py`
- [x] 6.4 Run `python -m pytest tests/test_engine/test_optimization.py -v`
- [x] 6.5 Run full test suite `python -m pytest tests/ -q` to verify no regressions
