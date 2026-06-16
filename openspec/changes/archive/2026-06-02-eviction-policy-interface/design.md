## Context

ChannelManager stores channel values in-memory. TOPIC channels append to lists that can grow unboundedly during long-running sessions. There's no mechanism to limit memory usage or evict stale data.

## Goals / Non-Goals

**Goals:**
- Define `EvictionPolicy` ABC with `should_evict` and `select_victim` methods
- Provide `NoEviction` (default, preserves current behavior) and `SizeBasedEviction` implementations
- Make eviction an optional ChannelManager parameter
- Keep engine zero-dependency

**Non-Goals:**
- Time-based eviction (P3+)
- Distributed eviction across nodes
- Eviction during checkpoint restore

## Decisions

### D1: EvictionPolicy is engine-internal

**Choice**: Create `engine/eviction.py` parallel to `engine/channel.py`.

**Rationale**: Eviction is a state management concern, not a service boundary.

### D2: Two-method interface

**Choice**: `should_evict(channel_name, current_size, context) -> bool` and `select_victim(items, max_count) -> list` (returns items to keep).

**Rationale**: Separating the decision (should we evict?) from the selection (which items to remove?) allows flexible policies.

### D3: SizeBasedEviction keeps most recent items

**Choice**: When size exceeds max, evict oldest items (keep newest `max_size`).

**Rationale**: For conversation history, recent messages are more relevant than old ones.

## Risks / Trade-offs

| Risk | Mitigation |
|------|-----------|
| Eviction removes data that might be needed | NoEviction is default; SizeBasedEviction is opt-in |
| Eviction adds overhead on every write | should_evict is O(1); select_victim only called when needed |
