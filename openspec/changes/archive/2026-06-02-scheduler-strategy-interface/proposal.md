## Why

The PregelRuntime currently executes `current_nodes` sequentially in a simple `for` loop (pregel.py line 141-151). This is fine for single-node or linear graphs, but limits throughput for parallel branches and prevents priority-based scheduling. A SchedulerStrategy interface allows swapping scheduling algorithms without modifying the runtime.

## What Changes

- Add a `SchedulerStrategy` ABC in `engine/scheduler.py` with methods for selecting next nodes and setting priorities
- Add a `FIFOScheduler` implementation (current behavior) for backward compatibility
- Register SchedulerStrategy as an optional parameter on PregelRuntime
- Do NOT modify the existing sequential loop — SchedulerStrategy is additive

## Capabilities

### New Capabilities
- `scheduler-strategy`: Pluggable scheduling interface for node execution ordering

### Modified Capabilities
- `engine-ports`: No change needed (SchedulerStrategy is engine-internal, not a service port)

## Impact

- **New file**: `src/hecate/engine/scheduler.py` (ABC + FIFOScheduler)
- **Modified file**: `src/hecate/engine/pregel.py` (add optional scheduler parameter)
- **New test**: `tests/test_engine/test_scheduler.py`
- **No breaking changes**: Existing behavior preserved as default
- **No new dependencies**: Uses only stdlib
