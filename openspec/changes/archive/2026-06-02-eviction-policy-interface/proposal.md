## Why

ChannelManager stores channel state in-memory with no eviction mechanism. TOPIC channels (append-only lists) can grow unboundedly during long-running sessions, eventually causing OOM. An EvictionPolicy interface allows pluggable eviction strategies without modifying ChannelManager.

## What Changes

- Add `EvictionPolicy` ABC in `engine/eviction.py` with methods for deciding when and which items to evict
- Add `NoEviction` implementation (current behavior — never evict)
- Add `SizeBasedEviction` implementation (evict oldest when list exceeds max size)
- Register EvictionPolicy as an optional parameter on ChannelManager
- Do NOT modify existing Channel behavior — EvictionPolicy is additive

## Capabilities

### New Capabilities
- `eviction-policy`: Pluggable eviction interface for channel state management

### Modified Capabilities
- None

## Impact

- **New file**: `src/hecate/engine/eviction.py` (ABC + NoEviction + SizeBasedEviction)
- **Modified file**: `src/hecate/engine/channel.py` (add optional eviction parameter)
- **New test**: `tests/test_engine/test_eviction.py`
- **No breaking changes**: Existing behavior preserved as default
- **No new dependencies**: Uses only stdlib
