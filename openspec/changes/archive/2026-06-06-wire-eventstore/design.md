## Context

EventStore ABC exists with append-only event persistence contract. InMemoryEventStore provides a test implementation. EventType enum defines 11 event categories. PregelRuntime executes compiled graphs in superstep cycles but has no event recording capability. Worker ABC has no constructor and no event recording mechanism. 8 production workers and 17 test stubs exist.

The roadmap identifies EventStore wiring as a Sprint 1 architecture hardening task (~20 LOC). Sprint 4 features (Full-Chain Tracing, Audit Logs) depend on this.

## Goals / Non-Goals

**Goals:**
- Wire EventStore into PregelRuntime for node lifecycle events (Plan A)
- Wire EventStore into Worker for execution detail events (Plan B)
- Follow constructor injection pattern (consistent with SchedulerStrategy, EvictionPolicy)
- Maintain full backward compatibility (no event_store = no recording)
- Enable future observability features without further interface changes

**Non-Goals:**
- Implementing PostgresEventStore or persistent storage (InMemoryEventStore is sufficient for now)
- Adding new EventType values (CUSTOM handles extensions)
- Event filtering or sampling (defer to Sprint 4)
- Changing Worker.execute() abstract method signature

## Decisions

### D1: Constructor injection for both PregelRuntime and Worker

**Choice**: `event_store: EventStore | None = None` in both constructors.

**Rationale**: Consistent with existing ABC wiring pattern. Optional parameter preserves backward compatibility.

### D2: Worker receives execution context via execute() parameter

**Choice**: Add `execution_context: dict | None = None` to Worker.execute(). PregelRuntime fills it with `{"session_id": UUID, "superstep": int, "event_store": EventStore}`.

**Rationale**: Worker needs session_id and superstep to create Events. These are PregelRuntime-level state that changes each superstep. Storing them as Worker state would require PregelRuntime to update Worker before each dispatch. A context dict passed at call time is cleaner and avoids mutable Worker state.

**Alternative**: Store session_id in Worker constructor. Rejected — superstep changes each iteration, so constructor injection alone is insufficient.

### D3: Worker ABC constructor accepts event_store but doesn't require it

**Choice**: Worker ABC gains `def __init__(self, event_store: EventStore | None = None)`. Subclasses that override __init__ should accept and pass through event_store.

**Rationale**: This gives Worker subclasses access to event_store for B-type events. Test stubs that don't override __init__ inherit the default (None) and work unchanged.

### D4: _emit() helper in PregelRuntime

**Choice**: Private method `_emit(session_id, event_type, node_id=None, payload=None)` that checks `if self._event_store` before appending.

**Rationale**: Reduces boilerplate — every event recording point becomes a single line instead of 5.

### D5: Event recording points in PregelRuntime

| Event Type | When | Payload |
|------------|------|---------|
| CUSTOM (SESSION_START) | Fresh execution start | `{"event_name": "SESSION_START", "initial_input_keys": [...]}` |
| RESUME | After checkpoint restore | `{"interrupted_node": str}` |
| NODE_START | Before worker dispatch | `{"node_type": str}` |
| NODE_END | After worker returns | `{"success": bool, "has_command": bool}` |
| ERROR | Before raising error | `{"error_type": str, "error_message": str}` |
| INTERRUPT | When interrupt detected | `{"interrupt_value_type": str}` |
| CHANNEL_WRITE | After _apply_writes | `{"channels": list[str]}` |
| CUSTOM (SUPERSTEP_END) | After checkpoint save | `{"event_name": "SUPERSTEP_END", "completed_nodes": int}` |

### D6: Event recording points in Worker (production workers only)

| Event Type | When | Worker |
|------------|------|--------|
| LLM_REQUEST | Before LLM call | LLMWorker |
| LLM_RESPONSE | After LLM response | LLMWorker |
| TOOL_CALL | Before tool invocation | ToolWorker |
| TOOL_RESULT | After tool result | ToolWorker |

Test stubs ignore execution_context and don't emit events.

## Risks / Trade-offs

- **25 files touched** (8 production workers + 17 test stubs for constructor change) → Most changes are mechanical (add `event_store=None` parameter)
- **Performance overhead** → EventStore.append() is async, called in async context. Negligible overhead for InMemoryEventStore. Production deployments can omit event_store to avoid overhead entirely.
- **execution_context dict is untyped** → Acceptable for now; can be refined to a dataclass later if needed
- **Test stubs need updating** → All 17 test Worker classes need to accept event_store in constructor. Mechanical change, no logic impact.
