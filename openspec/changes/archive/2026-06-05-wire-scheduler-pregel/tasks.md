## 1. PregelRuntime Constructor

- [x] 1.1 Add `scheduler: SchedulerStrategy | None = None` parameter to `PregelRuntime.__init__` in `src/hecate/engine/pregel.py`
- [x] 1.2 Store as `self._scheduler = scheduler or FIFOScheduler()` in the constructor body
- [x] 1.3 Add `from hecate.engine.scheduler import FIFOScheduler, SchedulerStrategy` import at the top of `pregel.py`

## 2. Superstep Loop Wiring

- [x] 2.1 In `execute()`, build `context = {"superstep": self._superstep, "channel_snapshot": snapshot}` after the snapshot line
- [x] 2.2 Insert `scheduled_nodes = self._scheduler.select_next(current_nodes, context)` before the `for node_id in current_nodes:` loop
- [x] 2.3 Change the loop to iterate `scheduled_nodes` instead of `current_nodes`

## 3. Tests

- [x] 3.1 Create `tests/test_engine/test_scheduler_integration.py` with a `TrackingScheduler` stub that records all `select_next` calls
- [x] 3.2 Test: default scheduler (no parameter) uses FIFOScheduler — verify execution completes identically to existing behavior
- [x] 3.3 Test: custom scheduler is called each superstep — verify `select_next` is invoked with correct node list and context dict
- [x] 3.4 Test: custom scheduler reorders nodes — provide a reversing scheduler and verify execution order differs from input
- [x] 3.5 Test: context dict contains `superstep` and `channel_snapshot` keys with correct values
- [x] 3.6 Test: single-node superstep still calls `select_next` (not skipped)
- [x] 3.7 Run `python -m pytest tests/test_engine/test_scheduler_integration.py -v` — all pass

## 4. Verification

- [x] 4.1 Run `ruff check src/hecate/engine/pregel.py tests/test_engine/test_scheduler_integration.py`
- [x] 4.2 Run `ruff format --check src/hecate/engine/pregel.py tests/test_engine/test_scheduler_integration.py`
- [x] 4.3 Run `mypy src/hecate/engine/pregel.py`
- [x] 4.4 Run `python -m pytest tests/ -q` — no regressions
