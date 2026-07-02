## Context

`SchedulerStrategy` ABC and `FIFOScheduler` exist in `engine/scheduler.py` (implemented in change `2026-06-02-scheduler-strategy-interface`) but are not wired into `PregelRuntime`. The runtime's `execute()` method iterates `current_nodes` directly at line 144:

```python
for node_id in current_nodes:
    # dispatch each node...
```

The published spec (`openspec/specs/scheduler-strategy/spec.md`) already declares "PregelRuntime SHALL accept an optional `scheduler` parameter" but the code does not implement this. This change closes that gap.

Two services instantiate `PregelRuntime`:
- `services/workflow/execution_service.py` L216
- `services/workflow/test_runner.py` L199

Both must remain compatible via the default parameter.

## Goals / Non-Goals

**Goals:**
- Wire `SchedulerStrategy` into `PregelRuntime` constructor and superstep loop
- Pass execution context (superstep number, channel snapshot) to `select_next`
- Verify wiring with integration tests that confirm the scheduler is called during execution
- Keep existing behavior identical (FIFOScheduler is identity function)

**Non-Goals:**
- Parallel execution of nodes (WorkerPool's responsibility)
- Async `select_next` (keep sync per engine zero-dependency principle)
- Typed context object (keep `dict` per YAGNI — no real scheduler implementations to drive field discovery)
- Scheduler awareness of FAN_OUT/MERGE node types (not needed — `_resolve_next_nodes()` guarantees same-superstep nodes are semantically independent)
- Dynamic weight changes during execution (P3+)

## Decisions

### D1: Scheduler is an optional constructor parameter with FIFOScheduler default

**Choice**: `scheduler: SchedulerStrategy | None = None` → stored as `self._scheduler = scheduler or FIFOScheduler()`

**Alternatives considered**:
- Required parameter → rejected: breaks existing instantiation sites
- No parameter, always FIFOScheduler → rejected: defeats the pluggability purpose

**Rationale**: Optional with default preserves backward compatibility. `None` sentinel avoids mutable default argument issues and makes intent explicit at call sites.

### D2: `select_next` called once per superstep, before the `for` loop

**Choice**: Replace `current_nodes` with `scheduled_nodes = self._scheduler.select_next(current_nodes, context)` at line 144, then iterate `scheduled_nodes`.

**Alternatives considered**:
- Call inside the loop per node → rejected: N calls for N nodes, overhead with no benefit
- Wrap the entire superstep block in a scheduler method → rejected: over-engineering, scheduler only needs to order, not orchestrate

**Rationale**: Single call is simple and matches `select_next`'s contract (takes list, returns ordered list). The scheduler reorders but does not filter — it always returns the same node IDs.

### D3: Context dict includes superstep number and channel snapshot

**Choice**: `context = {"superstep": self._superstep, "channel_snapshot": snapshot}`

**Alternatives considered**:
- Typed dataclass → rejected: YAGNI — no scheduler implementation exists to drive field discovery
- Empty dict → rejected: useless for any non-trivial scheduler
- Include graph metadata (node configs, edge list) → rejected: over-exposes engine internals

**Rationale**: Superstep number enables priority decay strategies. Channel snapshot enables content-aware scheduling. Both are cheap to provide. Future schedulers can ignore keys they don't need.

### D4: Scheduler does not filter or reject nodes

**Choice**: `select_next` MUST return the same set of node IDs (possibly reordered). The runtime does not validate this, but a scheduler that drops nodes would cause graph execution to stall.

**Rationale**: Filtering is a scheduling concern but we have no use case for it. If needed in P3, add a `filter_next` method rather than overloading `select_next`.

### D5: FAN_OUT/MERGE nodes passed through scheduler transparently

**Choice**: The scheduler receives all `current_nodes` including any FAN_OUT/MERGE nodes. The special handling inside the loop (L151-159) runs after scheduling.

**Rationale**: `_resolve_next_nodes()` guarantees that nodes in the same `current_nodes` list are semantically independent. FAN_OUT branches are dispatched by `_dispatch_fan_out()` internally — they don't appear in `current_nodes`. Reordering FAN_OUT/MERGE relative to regular nodes within the same superstep is safe by construction.

## Risks / Trade-offs

| Risk | Mitigation |
|------|-----------|
| Scheduler drops nodes, causing execution stall | Document contract clearly; FIFOScheduler guarantees passthrough; trust model for custom schedulers |
| Context dict keys may change in future | Document current keys; treat as advisory — schedulers should handle unknown keys gracefully |
| Overhead from scheduler call on single-node supersteps | FIFOScheduler is O(1) passthrough; negligible compared to LLM calls |
