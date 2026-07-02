## Context

GraphCompiler performs structural validation (entry, edges, reachability) and produces a CompiledGraph. For large graphs, there are optimization opportunities:
- Unreachable nodes waste memory and confuse debugging
- Parallel branches could be detected for concurrent execution
- Dead channels waste memory

## Goals / Non-Goals

**Goals:**
- Define `OptimizationPass` ABC with `optimize(graph) -> CompiledGraph`
- Provide `DeadNodeElimination` (removes unreachable nodes)
- Provide `ParallelBranchDetection` (marks parallel branches in metadata)
- Make optimization an optional GraphCompiler parameter
- Keep engine zero-dependency

**Non-Goals:**
- Modifying graph semantics (optimization is semantics-preserving)
- Distributed optimization
- Runtime optimization (compile-time only)

## Decisions

### D1: OptimizationPass is engine-internal

**Choice**: Create `engine/optimization.py` parallel to `engine/compiler.py`.

**Rationale**: Optimization is a compiler concern, not a service boundary.

### D2: Passes return new CompiledGraph (immutable)

**Choice**: `optimize(graph: CompiledGraph) -> CompiledGraph` returns a new graph, does not modify input.

**Rationale**: Functional style is safer and allows passes to be composed.

### D3: DeadNodeElimination removes unreachable nodes

**Choice**: Use the existing `_detect_unreachable` logic, but actually remove the nodes from the graph.

**Rationale**: Unreachable nodes are dead code. Removing them reduces memory and improves debuggability.

### D4: ParallelBranchDetection marks branches in metadata

**Choice**: Add `metadata["parallel_branches"]` to CompiledGraph with groups of nodes that can run in parallel.

**Rationale**: Detection is compile-time; actual parallelism is runtime (WorkerPool's job).

### D5: Compiler accepts optional pass list

**Choice**: `GraphCompiler(passes: list[OptimizationPass] | None = None)`.

**Rationale**: Opt-in behavior. Default is no optimization (backward compatible).

## Risks / Trade-offs

| Risk | Mitigation |
|------|-----------|
| DeadNodeElimination changes graph structure | Only removes truly unreachable nodes (validated by BFS) |
| ParallelBranchDetection may be conservative | Mark as metadata hint, not enforcement |
| Pass ordering matters | Document that passes run in list order |
