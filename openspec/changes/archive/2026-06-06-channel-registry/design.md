## Context

Channel write semantics are scattered across the engine layer. `Channel._initial_value()` and `Channel.write()` use `if/elif` chains against `ChannelType` enum values. `ChannelManager.write()` checks `ChannelType.TOPIC | PERSISTENT_TOPIC` for eviction eligibility. `ConflictResolver.resolve()` uses raw string comparison (`"last_value"`, `"topic"`, `"accumulator"`) with no reference to the enum at all. Adding a new channel type requires coordinated changes in 4 locations across 3 files with no structural guarantee of consistency.

Additionally, `PERSISTENT_TOPIC` is a separate enum value that has identical runtime behavior to `TOPIC` — persistence semantics are not implemented. Persistence is orthogonal to write behavior and should not be a separate type.

## Goals / Non-Goals

**Goals:**
- Consolidate all channel-type-specific behavior (initial value, write, eviction eligibility, conflict resolution) into a single `ChannelBehavior` ABC
- Provide a `ChannelTypeRegistry` that maps type name strings to `ChannelBehavior` instances
- Pre-register the 3 semantically distinct types (LAST_VALUE, TOPIC, ACCUMULATOR)
- Separate persistence from write semantics — `ChannelDef` gains `persistent: bool`
- Maintain backward compatibility for existing graph definitions using `"persistent_topic"`

**Non-Goals:**
- Implementing actual persistence semantics for `persistent=True` channels (P2)
- Building a plugin loading system for third-party channel behaviors
- Changing the `Channel` serialization/deserialization format beyond the PERSISTENT_TOPIC migration

## Decisions

### D1: ChannelBehavior is an ABC (not Protocol)

**Choice**: Use engine's existing ABC pattern (`abc.ABC`, `@abstractmethod`).

**Alternatives considered**:
- `typing.Protocol` — lighter weight but inconsistent with engine conventions (all other extensibility points use ABC: EnginePort, Worker, CheckpointStore, etc.)

**Rationale**: Consistency with existing engine ABC inventory. ChannelBehavior follows the same abstract/concrete pattern as EvictionPolicy, OptimizationPass, etc.

### D2: ChannelBehavior has 4 abstract methods

```python
class ChannelBehavior(ABC):
    @abstractmethod
    def initial_value(self, defn: ChannelDef) -> Any: ...
    
    @abstractmethod
    def write(self, current: Any, value: Any, defn: ChannelDef) -> Any: ...
    
    @abstractmethod
    def is_evictable(self) -> bool: ...
    
    @abstractmethod
    def resolve_conflict(self, current: Any, proposed: Any) -> Any: ...
```

**Rationale**: These 4 methods cover every place channel type is currently checked. `write()` returns the new value (immutable-style) rather than mutating in place, which is cleaner for testing and conflict resolution.

### D3: ChannelTypeRegistry is a module-level singleton

**Choice**: Module-level `_REGISTRY: dict[str, ChannelBehavior]` with `register()`, `get()`, and `list_types()` functions.

**Alternatives considered**:
- Class-based singleton — unnecessary indirection for what is essentially a dict
- Instance on ChannelManager — each manager would need to accept and forward the registry

**Rationale**: Matches the existing `_STRATEGY_REGISTRY` pattern in `services/context/provider_shaping.py`. Module-level registry is simple, testable, and consistent with the project's existing patterns.

### D4: PERSISTENT_TOPIC becomes a deprecated alias

**Choice**: `parse_graph()` auto-migrates `"persistent_topic"` to `"topic"` with `persistent=True`. The `ChannelType` enum retains `PERSISTENT_TOPIC = "persistent_topic"` for backward compatibility but it maps to `TopicBehavior` in the registry.

**Alternatives considered**:
- Remove `PERSISTENT_TOPIC` entirely (BREAKING without migration path)
- Keep `PERSISTENT_TOPIC` as separate behavior (doesn't solve the conflation problem)

**Rationale**: Migration path preserves existing graph definitions. The enum value still exists so code that references it compiles, but the registry maps it to the same behavior as TOPIC.

### D5: ChannelDef gains persistent: bool

**Choice**: Add `persistent: bool = False` to the `ChannelDef` dataclass. JSON Schema gains a `"persistent"` boolean property. Eviction, write, and conflict resolution ignore this flag (persistence is handled by checkpoint layer).

**Rationale**: Persistence is a storage concern, not a write-semantics concern. Separating them allows any channel type to be persistent in the future.

### D6: ConflictResolver delegates to ChannelBehavior

**Choice**: `ConflictResolver.resolve()` accepts a `ChannelBehavior` instead of `channel_type: str`. The caller (PregelRuntime) looks up the behavior from the registry.

**Rationale**: Eliminates the string-based dispatch that duplicates channel type logic. Conflict resolution is now driven by the same behavior objects that drive writes.

## Risks / Trade-offs

| Risk | Mitigation |
|------|-----------|
| Behavior objects are stateless — `write()` takes `(current, value, defn)` and returns new value. Channel must store the result. | Clean functional interface; easy to test |
| Module-level singleton makes testing slightly harder | Tests can call `register()` to override; or use a test-specific setup |
| `persistent_topic` migration in `parse_graph()` adds complexity | One-time auto-migration with deprecation warning; simple string substitution |
| ~10 test files need ChannelType reference updates | Mechanical changes; each test just uses the new API |
