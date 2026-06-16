## Why

Channel behavior (write semantics, initial values, eviction eligibility, conflict resolution) is scattered across three files using three different matching mechanisms (enum comparison, `in` tuple check, raw string equality). This makes the system fragile — adding a new channel type requires touching `Channel._initial_value()`, `Channel.write()`, `ChannelManager.write()`, and `ConflictResolver.resolve()` independently, with no guarantee they stay consistent. Additionally, `PERSISTENT_TOPIC` is a separate type with identical behavior to `TOPIC` because persistence is conflated with write semantics.

## What Changes

- **Introduce `ChannelBehavior` ABC** — encapsulates write, initial value, eviction eligibility, and conflict resolution for a channel type in a single object
- **Introduce `ChannelTypeRegistry`** — maps type name strings to `ChannelBehavior` instances, with built-in registrations for the 4 existing types
- **Replace if/elif dispatch in `Channel` and `ChannelManager`** with behavior delegation
- **Replace string-based dispatch in `ConflictResolver`** with behavior delegation
- **Convert `PERSISTENT_TOPIC` to `persistent: bool` on `ChannelDef`** — persistence is orthogonal to write semantics; `PERSISTENT_TOPIC` becomes `TOPIC` + `persistent=True`
- **BREAKING**: Remove `ChannelType.PERSISTENT_TOPIC` enum value; existing graph definitions using `"persistent_topic"` will be auto-migrated to `"topic"` + `persistent: true` during `parse_graph()`
- **Update JSON Schema** — add `persistent` boolean property to channel definitions, keep `persistent_topic` as deprecated alias

## Capabilities

### New Capabilities
- `channel-registry`: Registry pattern for channel types with pluggable behaviors (write, initial value, eviction, conflict resolution)

### Modified Capabilities
- `engine-types`: ChannelDef gains `persistent: bool` field; ChannelType loses PERSISTENT_TOPIC
- `graph-dsl`: parse_graph() migrates deprecated "persistent_topic" to "topic" + persistent=True; JSON Schema updated

## Impact

- **Engine layer**: `types.py` (ChannelDef, ChannelType), `channel.py` (Channel, ChannelManager), `graph_dsl.py` (parse_graph)
- **Temporal layer**: `conflict.py` (ConflictResolver) — switches from string dispatch to behavior delegation
- **Runtime**: `pregel.py` — minor changes to channel type string passing
- **Templates**: `templates.py` — all PERSISTENT_TOPIC references become TOPIC + persistent=True
- **JSON Schema**: `schemas/graph-dsl.schema.json` — add `persistent` property, keep `persistent_topic` as deprecated
- **Tests**: ~10 test files reference ChannelType; all need updates
