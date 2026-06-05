## Why

`SchedulerStrategy` ABC and `FIFOScheduler` are defined in `engine/scheduler.py` but never used by `PregelRuntime`. The runtime still iterates `current_nodes` directly in a `for` loop (pregel.py L144). This disconnect means the published spec (`openspec/specs/scheduler-strategy/spec.md`) promises behavior ("PregelRuntime SHALL accept an optional `scheduler` parameter") that the code does not deliver. Wiring the scheduler into the runtime closes this spec-implementation gap and prepares the engine for P2 parallel WorkerPool scheduling.

## What Changes

- Add optional `scheduler: SchedulerStrategy | None = None` parameter to `PregelRuntime.__init__`, defaulting to `FIFOScheduler()`
- Insert `self._scheduler.select_next(current_nodes, context)` call before the `for node_id in current_nodes:` loop in `execute()`
- Build the `context` dict with `superstep` and `channel_snapshot` keys
- Update `services/workflow/execution_service.py` and `services/workflow/test_runner.py` (the two PregelRuntime instantiation sites) to remain compatible via the default parameter
- Add tests verifying scheduler is called during execution and that node order can be customized

## Capabilities

### New Capabilities

(none — SchedulerStrategy ABC already exists)

### Modified Capabilities

- `scheduler-strategy`: Extend the published spec to add requirements for how PregelRuntime calls the scheduler during the superstep loop (constructor wiring, context dict contents, call site semantics)

## Impact

- **Modified file**: `src/hecate/engine/pregel.py` — add scheduler parameter + select_next call
- **Modified file**: `openspec/specs/scheduler-strategy/spec.md` — add wiring requirements
- **New test**: `tests/test_engine/test_scheduler_integration.py` — verify scheduler is invoked during PregelRuntime.execute()
- **No breaking changes**: Default FIFOScheduler preserves identical behavior
- **No new dependencies**: Uses existing SchedulerStrategy from `engine/scheduler.py`
