## 1. SchedulerStrategy ABC

- [x] 1.1 Create `src/hecate/engine/scheduler.py` with `SchedulerStrategy(ABC)` defining abstract methods: `select_next(nodes: list[str], context: dict) -> list[str]`, `set_weights(weights: dict[str, float]) -> None`
- [x] 1.2 Add full docstrings to SchedulerStrategy ABC and each abstract method (English, matching existing EnginePort/CheckpointStore style)

## 2. FIFOScheduler Implementation

- [x] 2.1 Implement `FIFOScheduler(SchedulerStrategy)` that returns nodes in input order
- [x] 2.2 `select_next` returns the input list unchanged
- [x] 2.3 `set_weights` is a no-op (stores nothing, FIFO ignores weights)
- [x] 2.4 Add docstrings to FIFOScheduler

## 3. PregelRuntime Integration

- [x] 3.1 Add optional `scheduler: SchedulerStrategy | None = None` parameter to `PregelRuntime.__init__`
- [x] 3.2 Default to `FIFOScheduler()` if no scheduler provided
- [x] 3.3 In the superstep loop, call `scheduler.select_next(current_nodes, context)` before dispatching workers
- [x] 3.4 Ensure existing behavior is preserved (nodes execute in the order returned by scheduler)

## 4. Tests

- [x] 4.1 Create `tests/test_engine/test_scheduler.py`
- [x] 4.2 Test FIFOScheduler.select_next returns nodes unchanged
- [x] 4.3 Test FIFOScheduler.set_weights is no-op
- [x] 4.4 Test FIFOScheduler with empty node list
- [x] 4.5 Test SchedulerStrategy is abstract (cannot instantiate directly)
- [x] 4.6 Test PregelRuntime default scheduler is FIFOScheduler
- [x] 4.7 Test PregelRuntime with custom scheduler receives correct nodes

## 5. Verification

- [x] 5.1 Run `ruff check src/hecate/engine/scheduler.py src/hecate/engine/pregel.py tests/test_engine/test_scheduler.py`
- [x] 5.2 Run `ruff format --check src/hecate/engine/scheduler.py src/hecate/engine/pregel.py tests/test_engine/test_scheduler.py`
- [x] 5.3 Run `mypy src/hecate/engine/scheduler.py src/hecate/engine/pregel.py`
- [x] 5.4 Run `python -m pytest tests/test_engine/test_scheduler.py -v`
- [x] 5.5 Run full test suite `python -m pytest tests/ -q` to verify no regressions
