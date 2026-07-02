## Why

EvictionPolicy ABC and its implementations (NoEviction, SizeBasedEviction) are defined but not wired into ChannelManager. TOPIC channels grow unboundedly during long-running sessions with no mechanism to limit memory usage. The spec at `openspec/specs/eviction-policy/spec.md` already defines the integration contract (ChannelManager accepts optional eviction_policy, applies on TOPIC writes) — this change implements that wiring.

## What Changes

- Wire EvictionPolicy into ChannelManager: accept optional `eviction_policy` parameter (default `NoEviction`), apply eviction after TOPIC/PERSISTENT_TOPIC writes
- Wire EvictionPolicy into PregelRuntime: accept optional `eviction_policy` parameter, pass through to ChannelManager constructor
- Do NOT apply eviction during `ChannelManager.restore()` — restore must reproduce exact checkpoint state
- Add tests for the wired integration: ChannelManager with eviction, PregelRuntime with eviction

## Capabilities

### New Capabilities

(none)

### Modified Capabilities

- `eviction-policy`: Add requirement that ChannelManager and PregelRuntime accept and use EvictionPolicy (currently spec only covers ABC definition, not wiring)
- `pregel-runtime`: Add `eviction_policy` optional constructor parameter

## Impact

- `src/hecate/engine/channel.py`: ChannelManager constructor + write method
- `src/hecate/engine/pregel.py`: PregelRuntime constructor (pass through eviction_policy)
- `tests/test_engine/test_eviction.py`: Add integration tests for ChannelManager eviction
- `tests/test_engine/test_pregel.py`: Add test for PregelRuntime with eviction enabled
