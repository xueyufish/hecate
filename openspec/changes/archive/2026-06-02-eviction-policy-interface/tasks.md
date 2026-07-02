## 1. EvictionPolicy ABC

- [x] 1.1 Create `src/hecate/engine/eviction.py` with `EvictionPolicy(ABC)` defining abstract methods: `should_evict(channel_name: str, current_size: int, context: dict) -> bool`, `select_victim(items: list, max_count: int) -> list`
- [x] 1.2 Add full docstrings to EvictionPolicy ABC and each abstract method

## 2. NoEviction Implementation

- [x] 2.1 Implement `NoEviction(EvictionPolicy)` that always returns False from should_evict
- [x] 2.2 `select_victim` returns items unchanged
- [x] 2.3 Add docstrings

## 3. SizeBasedEviction Implementation

- [x] 3.1 Implement `SizeBasedEviction(EvictionPolicy)` with `max_size: int` parameter
- [x] 3.2 `should_evict` returns True when current_size >= max_size
- [x] 3.3 `select_victim` returns the last max_count items (keep newest)
- [x] 3.4 Add docstrings

## 4. ChannelManager Integration

- [x] 4.1 Add optional `eviction_policy: EvictionPolicy | None = None` parameter to `ChannelManager.__init__`
- [x] 4.2 Default to `NoEviction()` if no policy provided
- [x] 4.3 In `Channel.write()`, after appending to TOPIC channels, check `should_evict` and apply `select_victim` if needed
- [x] 4.4 Only apply eviction to TOPIC/PERSISTENT_TOPIC channels (not LAST_VALUE or ACCUMULATOR)

## 5. Tests

- [x] 5.1 Create `tests/test_engine/test_eviction.py`
- [x] 5.2 Test EvictionPolicy is abstract
- [x] 5.3 Test NoEviction.should_evict always returns False
- [x] 5.4 Test NoEviction.select_victim returns all items
- [x] 5.5 Test SizeBasedEviction.should_evict below/above threshold
- [x] 5.6 Test SizeBasedEviction.select_victim keeps newest items
- [x] 5.7 Test ChannelManager default uses NoEviction
- [x] 5.8 Test ChannelManager with SizeBasedEviction evicts on TOPIC write

## 6. Verification

- [x] 6.1 Run `ruff check src/hecate/engine/eviction.py src/hecate/engine/channel.py tests/test_engine/test_eviction.py`
- [x] 6.2 Run `ruff format --check src/hecate/engine/eviction.py src/hecate/engine/channel.py tests/test_engine/test_eviction.py`
- [x] 6.3 Run `mypy src/hecate/engine/eviction.py src/hecate/engine/channel.py`
- [x] 6.4 Run `python -m pytest tests/test_engine/test_eviction.py -v`
- [x] 6.5 Run full test suite `python -m pytest tests/ -q` to verify no regressions
