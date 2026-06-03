## Context

Hecate's engine layer currently persists execution state via `CheckpointStore` — a snapshot-based model that captures the full channel state, superstep counter, and pending writes at discrete points. This design works for pause/resume (interrupt) but cannot support:

- **Fine-grained audit trails**: Checkpoints are opaque blobs; you cannot see *which* tool call produced *which* intermediate result.
- **Incremental state reconstruction**: Loading a checkpoint requires the full snapshot; partial replay from a known point is impossible.
- **Event-driven debugging**: No way to subscribe to state changes as they happen.
- **Replay-based testing**: Cannot replay an execution from event N to verify behavior.

The engine's architecture is port-and-adapter: `EnginePort` (ABC) decouples the engine from services. `CheckpointStore` (ABC) decouples persistence. Adding `EventStore` follows this exact pattern.

## Goals / Non-Goals

**Goals:**
- Define an `EventStore` ABC with append, query, and replay methods
- Define an `Event` dataclass that captures granular execution state (node start/end, tool call/result, channel write, LLM request/response)
- Provide an `InMemoryEventStore` for testing
- Reserve the interface on `EnginePort` as an optional property
- Keep the engine zero-dependency (no external libraries)

**Non-Goals:**
- Postgres-backed EventStore implementation (P3)
- Integration with PregelRuntime to emit events automatically (P3, when GuardrailHook is implemented)
- Event schema versioning or migration
- Event compaction or retention policies
- Distributed event streaming (Kafka/NATS)

## Decisions

### D1: EventStore is a standalone ABC, not a CheckpointStore extension

**Choice**: Separate `EventStore` ABC in its own module (`engine/eventstore.py`), parallel to `engine/checkpoint.py`.

**Alternatives considered**:
- Extending `CheckpointStore` with event methods → rejected: CheckpointStore is snapshot-oriented; events are a fundamentally different access pattern (append-only vs overwrite)
- Adding event methods to `EnginePort` → rejected: EventStore is an engine-internal concern, not a service boundary

**Rationale**: Mirrors the CheckpointStore pattern exactly. Clear separation of concerns. Each can evolve independently.

### D2: Event is a frozen dataclass with typed fields

**Choice**: Use `@dataclass(frozen=True)` with explicit typed fields: `event_type`, `session_id`, `superstep`, `node_id`, `timestamp`, `payload`.

**Alternatives considered**:
- Dict-based events → rejected: no type safety, easy to introduce typos
- Pydantic model → rejected: adds dependency in engine layer (engine must be zero-dep)

**Rationale**: Frozen dataclass is immutable, hashable, type-safe, and requires no external dependencies. Matches engine layer constraints.

### D3: Event types as string enum, not class hierarchy

**Choice**: `EventType` string enum (`NODE_START`, `NODE_END`, `TOOL_CALL`, `TOOL_RESULT`, `CHANNEL_WRITE`, `LLM_REQUEST`, `LLM_RESPONSE`, `INTERRUPT`, `RESUME`, `CUSTOM`).

**Alternatives considered**:
- Event subclass per type → rejected: over-engineering for an interface reservation
- Free-form strings → rejected: no discoverability

**Rationale**: Enum is explicit and extensible (CUSTOM for user events). Easy to serialize. No class hierarchy overhead.

### D4: EventStore is an optional EnginePort property, not a required parameter

**Choice**: Add `event_store` as an optional property on `EnginePort` with a default of `None`. The engine checks `if port.event_store is not None` before appending.

**Rationale**: P2 is interface reservation only. No production code should emit events yet. Making it optional ensures zero disruption to existing flows.

### D5: Replay returns an AsyncGenerator, not a list

**Choice**: `async def replay(session_id, from_version) -> AsyncGenerator[Event, None]`

**Rationale**: Large sessions may have thousands of events. AsyncGenerator avoids loading all into memory. Consistent with the engine's async-first design.

## Risks / Trade-offs

| Risk | Mitigation |
|------|-----------|
| Event schema may need to change in P3 when GuardrailHook emits events | P2 only defines the interface; InMemoryEventStore is the only implementation and can be freely modified |
| `EventType` enum may be insufficient for future event types | `CUSTOM` type with arbitrary `payload` dict covers unknown cases |
| Optional property on EnginePort adds branching logic | Single `if event_store` check is negligible; can be removed when EventStore becomes required in P3 |
| InMemoryEventStore grows unbounded in long-running tests | Document that it's for testing only; production PostgresEventStore (P3) will have retention |

## Open Questions

None — this is a well-bounded interface reservation with clear precedent (CheckpointStore).
