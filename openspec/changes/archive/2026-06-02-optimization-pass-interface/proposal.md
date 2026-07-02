## Why

The GraphCompiler currently only validates graph structure (entry, edges, reachability) but performs no optimization. For large graphs (50+ nodes), this leaves performance on the table:
- Unreachable nodes are detected but not removed
- Parallel branches are not identified
- Channels written but never read waste memory

An OptimizationPass interface allows pluggable graph optimizations after validation.

## What Changes

- Add `OptimizationPass` ABC in `engine/optimization.py` with `optimize(graph) -> CompiledGraph`
- Add `DeadNodeElimination` pass (removes unreachable nodes)
- Add `ParallelBranchDetection` pass (marks independent branches for parallel execution)
- Register optimization passes as optional parameter on GraphCompiler
- Do NOT change default behavior — optimization is opt-in

## Capabilities

### New Capabilities
- `optimization-pass`: Pluggable graph optimization interface

### Modified Capabilities
- None

## Impact

- **New file**: `src/hecate/engine/optimization.py` (ABC + 2 implementations)
- **Modified file**: `src/hecate/engine/compiler.py` (add optional passes parameter)
- **New test**: `tests/test_engine/test_optimization.py`
- **No breaking changes**: Existing behavior preserved as default
- **No new dependencies**: Uses only stdlib
