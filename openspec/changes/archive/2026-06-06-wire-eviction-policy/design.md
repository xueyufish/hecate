## Context

EvictionPolicy ABC (NoEviction, SizeBasedEviction) was implemented in `engine/eviction.py` with full test coverage. ChannelManager (`engine/channel.py`) manages typed channels but has no eviction mechanism. The spec at `openspec/specs/eviction-policy/spec.md` already defines the wiring contract — ChannelManager accepts optional `eviction_policy` parameter — but the code does not implement it yet.

The pattern is identical to the SchedulerStrategy wiring (completed): PregelRuntime accepts an optional parameter, defaults to the no-op implementation, passes it through to the internal component.

## Goals / Non-Goals

**Goals:**
- Wire EvictionPolicy into ChannelManager constructor and write method
- Wire EvictionPolicy into PregelRuntime constructor (pass-through to ChannelManager)
- Only apply eviction to TOPIC and PERSISTENT_TOPIC channels (LAST_VALUE overwrites, ACCUMULATOR reduces — neither grows unboundedly)
- Preserve exact checkpoint state during restore (no eviction on restore)

**Non-Goals:**
- Per-channel eviction configuration (global policy only for P1)
- Time-based or token-based eviction policies
- Context injection into should_evict (pass empty dict `{}`)
- Distributed eviction across nodes

## Decisions

### D1: Injection point is ChannelManager.__init__

**Choice**: ChannelManager accepts `eviction_policy: EvictionPolicy | None = None`, defaults to `NoEviction()`.

**Rationale**: Mirrors the SchedulerStrategy pattern. ChannelManager is the state container that performs writes, so it owns eviction decisions.

### D2: Eviction triggers after write, only for TOPIC channels

**Choice**: In `ChannelManager.write()`, after `channel.write(value)`, check if channel type is TOPIC/PERSISTENT_TOPIC. If so, call `should_evict()`. If True, replace `channel._value` with `select_victim()` result.

**Rationale**: Only TOPIC channels grow unboundedly (append-only list). LAST_VALUE overwrites, ACCUMULATOR reduces. Eviction is only meaningful for list-valued channels.

### D3: No eviction during restore

**Choice**: `ChannelManager.restore()` is not modified. It directly sets `_value` to reproduce exact checkpoint state.

**Rationale**: restore() intentionally bypasses write semantics. Eviction during restore would corrupt checkpoint state.

### D4: PregelRuntime pass-through

**Choice**: PregelRuntime.__init__ accepts `eviction_policy: EvictionPolicy | None = None` and passes it to `ChannelManager(eviction_policy=eviction_policy or NoEviction())`.

**Rationale**: Identical pattern to SchedulerStrategy and conflict_resolver — constructor injection with None default.

## Risks / Trade-offs

| Risk | Mitigation |
|------|-----------|
| Eviction removes data needed for downstream nodes | NoEviction is default; SizeBasedEviction is opt-in |
| Eviction adds overhead on every TOPIC write | should_evict is O(1); select_victim only called when threshold exceeded |
| Channel._value is accessed directly (not via method) | Acceptable — eviction is a state management concern internal to ChannelManager |
