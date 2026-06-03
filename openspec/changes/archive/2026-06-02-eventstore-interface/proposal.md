## Why

The engine currently uses a **snapshot-based** checkpoint model (`CheckpointStore.save/load`) that captures full channel state at discrete superstep boundaries. This is sufficient for pause/resume, but precludes several P2+ capabilities: fine-grained audit trails, incremental state reconstruction, event-driven debugging, and replay-based testing. An append-only EventStore interface provides the foundation for these capabilities with minimal implementation cost (ABC only in P2).

## What Changes

- Add a new `EventStore` ABC in `engine/eventstore.py` with methods for appending, querying, and replaying events
- Add an `InMemoryEventStore` implementation for testing
- Register EventStore as an optional engine dependency alongside CheckpointStore
- Do NOT modify existing CheckpointStore or PregelRuntime — EventStore is additive and independently usable

## Capabilities

### New Capabilities
- `eventstore`: Append-only event persistence interface for fine-grained execution state tracking

### Modified Capabilities
- `engine-ports`: Add optional `event_store` property to EnginePort for P2 interface reservation

## Impact

- **New file**: `src/hecate/engine/eventstore.py` (ABC + InMemoryEventStore)
- **Modified file**: `src/hecate/engine/ports.py` (add optional `event_store` property)
- **New test**: `tests/test_engine/test_eventstore.py`
- **No breaking changes**: EventStore is entirely additive; no existing code requires modification
- **No new dependencies**: Uses only stdlib (`abc`, `uuid`, `dataclasses`)
