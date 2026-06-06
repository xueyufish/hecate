## 1. ChannelManager Eviction Wiring

- [x] 1.1 Modify `ChannelManager.__init__()` in `src/hecate/engine/channel.py` — add `eviction_policy: EvictionPolicy | None = None` parameter, store as `self._eviction_policy = eviction_policy or NoEviction()`, import from `hecate.engine.eviction`
- [x] 1.2 Modify `ChannelManager.write()` — after `self._channels[name].write(value)`, check if channel type is TOPIC or PERSISTENT_TOPIC; if so, get `channel = self._channels[name]`, call `self._eviction_policy.should_evict(name, len(channel._value), {})`, if True set `channel._value = self._eviction_policy.select_victim(channel._value)`

## 2. PregelRuntime Pass-through

- [x] 2.1 Modify `PregelRuntime.__init__()` in `src/hecate/engine/pregel.py` — add `eviction_policy: EvictionPolicy | None = None` parameter, import from `hecate.engine.eviction`
- [x] 2.2 Change `self._channel_manager = ChannelManager()` to `self._channel_manager = ChannelManager(eviction_policy=eviction_policy or NoEviction())`

## 3. Tests

- [x] 3.1 Add test `test_channel_manager_default_no_eviction` in `tests/test_engine/test_eviction.py` — create ChannelManager without eviction_policy, write 100 items to TOPIC channel, verify all 100 present
- [x] 3.2 Add test `test_channel_manager_size_based_eviction` — create ChannelManager with SizeBasedEviction(max_size=5), write 10 items to TOPIC channel, verify only 5 newest remain
- [x] 3.3 Add test `test_channel_manager_eviction_skips_last_value` — create ChannelManager with SizeBasedEviction(max_size=3), write multiple times to LAST_VALUE channel, verify all writes succeed without eviction
- [x] 3.4 Add test `test_channel_manager_eviction_skips_accumulator` — create ChannelManager with SizeBasedEviction(max_size=3), write to ACCUMULATOR channel, verify value is sum of all writes
- [x] 3.5 Add test `test_channel_manager_restore_bypasses_eviction` — create ChannelManager with SizeBasedEviction(max_size=3), restore a snapshot with 10 items in TOPIC channel, verify all 10 present (no eviction)
- [x] 3.6 Add test `test_pregel_runtime_eviction_passthrough` in `tests/test_engine/test_pregel.py` — create PregelRuntime with SizeBasedEviction(max_size=3), execute a graph that writes 5 items to a TOPIC channel, verify channel only has 3 newest items

## 4. Verification

- [x] 4.1 Run `ruff check src/hecate/ tests/`
- [x] 4.2 Run `ruff format --check src/ tests/`
- [x] 4.3 Run `mypy src/`
- [x] 4.4 Run `python -m pytest tests/ -q` — no regressions
