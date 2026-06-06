## Why

OptimizationPass ABC and its two implementations (DeadNodeElimination, ParallelBranchDetection) exist in `engine/optimization.py` but are never invoked during compilation. GraphCompiler produces a CompiledGraph after validation but skips the optimization phase entirely. This leaves unreachable nodes in the graph at runtime and misses opportunities for parallel branch detection. Wiring OptimizationPass into GraphCompiler completes the Sprint 1 architecture hardening goal of connecting all defined engine ABCs.

## What Changes

- GraphCompiler gains an `__init__` accepting an optional `passes` parameter (list of OptimizationPass, default empty)
- After validation and CompiledGraph construction, each pass is applied in sequence via `optimize()`
- All existing `GraphCompiler()` instantiations remain backward-compatible (no-arg constructor still works)

## Capabilities

### New Capabilities

_(none)_

### Modified Capabilities

- `optimization-pass`: GraphCompiler SHALL accept optional optimization passes and apply them after validation

## Impact

- `src/hecate/engine/compiler.py` — add constructor, apply passes in `compile()`
- `tests/test_engine/test_graph_dsl.py` — add tests for optimization pass integration
- Services (`workflow_service.py`, `execution_service.py`, `test_runner.py`) — no changes needed (backward-compatible)
- `src/hecate/engine/optimization.py` — no changes (ABC + implementations already exist)
