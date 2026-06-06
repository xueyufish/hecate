## Context

GraphCompiler is currently a stateless class — no constructor, single `compile()` method. It validates a GraphConfig and returns a CompiledGraph. The engine defines `OptimizationPass` ABC with two implementations (`DeadNodeElimination`, `ParallelBranchDetection`) but they are never invoked. The compiler's `_detect_unreachable()` only logs warnings without acting on them.

Three service-layer callers instantiate `GraphCompiler()` with no arguments: `workflow_service.py`, `execution_service.py`, and `test_runner.py`. Sixteen test files also create inline `GraphCompiler()` instances.

## Goals / Non-Goals

**Goals:**
- Wire OptimizationPass into GraphCompiler via constructor injection
- Apply optimization passes after validation, before returning CompiledGraph
- Maintain full backward compatibility (no-arg constructor = current behavior)
- Follow the existing ABC wiring pattern (SchedulerStrategy → PregelRuntime, EvictionPolicy → ChannelManager)

**Non-Goals:**
- Adding a `metadata` field to CompiledGraph (ParallelBranchDetection writes to it but that's a pre-existing issue)
- Refactoring `_detect_unreachable()` into DeadNodeElimination (different purposes — warning vs elimination)
- Changing service-layer code to inject passes (services use defaults for now)
- Implementing new optimization passes

## Decisions

### D1: `passes` is a list, not a single OptimizationPass

**Choice**: `passes: list[OptimizationPass] | None = None`

**Rationale**: The existing spec (`openspec/specs/optimization-pass/spec.md`) prescribes a list parameter. Multiple passes form a pipeline — e.g., eliminate dead nodes first, then detect parallel branches in the cleaned graph. A single-pass API would require composition for this common case.

**Alternative**: Single `optimization: OptimizationPass | None = None` (matches SchedulerStrategy pattern). Rejected — doesn't match spec, less flexible.

### D2: Default is empty list (no optimization)

**Choice**: `self._passes = passes or []`

**Rationale**: Current behavior is no optimization. Empty list = identity transformation. No no-op default class needed (unlike `NoEviction`/`FIFOScheduler`).

### D3: Passes run after CompiledGraph construction

**Choice**: Build CompiledGraph first, then apply passes sequentially.

```python
graph = CompiledGraph(nodes=config.nodes, edges=config.edges, ...)
for p in self._passes:
    graph = p.optimize(graph)
return graph
```

**Rationale**: OptimizationPass operates on `CompiledGraph`, not `GraphConfig`. The validation pipeline ensures the graph is structurally sound before any optimization touches it. This matches the OptimizationPass ABC contract: `optimize(graph: CompiledGraph) -> CompiledGraph`.

### D4: Keep `_detect_unreachable` alongside DeadNodeElimination

**Rationale**: `_detect_unreachable()` logs warnings during development (useful even without optimization passes). `DeadNodeElimination` actually removes nodes. Different purposes, both valuable. The BFS duplication is acceptable — they operate on different types (`GraphConfig` vs `CompiledGraph`).

## Risks / Trade-offs

- **BFS runs twice when DeadNodeElimination is wired** → Acceptable overhead; compiler runs once per graph definition, not per execution step
- **Pass ordering is caller's responsibility** → Document in docstring that passes execute in list order
- **ParallelBranchDetection writes to nonexistent `metadata` field** → Pre-existing bug, not introduced by this change; out of scope
