## Context

PregelRuntime executes graph nodes in superstep cycles. Each superstep has a set of `current_nodes` that need execution. Currently, the runtime iterates through them sequentially:

```python
for node_id in current_nodes:
    result = await self._pool.dispatch(worker, node_id, node.config, snapshot)
    results.append(result)
```

This simple approach works but:
- Cannot prioritize critical nodes over background tasks
- Cannot implement fair-sharing for multi-tenant scenarios
- Cannot reorder based on runtime conditions (e.g., cache hits)

## Goals / Non-Goals

**Goals:**
- Define `SchedulerStrategy` ABC with `select_next` and `set_weights` methods
- Provide `FIFOScheduler` as default (preserves current behavior)
- Make scheduler an optional PregelRuntime parameter
- Keep engine zero-dependency

**Non-Goals:**
- Parallel execution of nodes (that's WorkerPool's job)
- Distributed scheduling across nodes
- Dynamic priority changes during execution (P3+)

## Decisions

### D1: SchedulerStrategy is engine-internal, not a service port

**Choice**: Create `engine/scheduler.py` parallel to `engine/worker.py`.

**Rationale**: Scheduling is an engine concern (how to order node execution), not a service boundary (how to call LLM/tools). It doesn't belong in EnginePort.

### D2: select_next returns ordered list, not single node

**Choice**: `select_next(nodes: list[str], context: dict) -> list[str]`

**Alternatives considered**:
- Return one node at a time → rejected: requires N calls for N nodes, adds overhead
- Return generator → rejected: over-engineering for current needs

**Rationale**: Returning an ordered list is simple and allows the runtime to process nodes in the suggested order.

### D3: FIFOScheduler preserves exact current behavior

**Choice**: FIFOScheduler returns nodes in the order they were received.

**Rationale**: Zero-risk default. Existing tests pass without modification.

### D4: set_weights is a no-op in FIFOScheduler

**Choice**: FIFOScheduler ignores weights (FIFO doesn't use priorities).

**Rationale**: The method exists for future PriorityScheduler (P3) to implement.

## Risks / Trade-offs

| Risk | Mitigation |
|------|-----------|
| Adding scheduler parameter to PregelRuntime changes constructor | Default to FIFOScheduler, existing code works unchanged |
| Scheduler adds overhead for simple graphs | FIFOScheduler is O(1) passthrough |

## Open Questions

None.
