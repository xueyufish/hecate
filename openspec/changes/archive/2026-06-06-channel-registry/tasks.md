## 1. ChannelBehavior ABC

- [x] 1.1 Define `ChannelBehavior` ABC in `src/hecate/engine/channel.py` with abstract methods: `initial_value(defn) -> Any`, `write(current, value, defn) -> Any`, `is_evictable() -> bool`, `resolve_conflict(current, proposed) -> Any`
- [x] 1.2 Implement `LastValueBehavior` — `initial_value` returns `defn.default or None`, `write` returns `value`, `is_evictable` returns `False`, `resolve_conflict` returns `proposed` (last-write-wins)
- [x] 1.3 Implement `TopicBehavior` — `initial_value` returns `[]`, `write` appends/extends, `is_evictable` returns `True`, `resolve_conflict` deduplicates and merges lists
- [x] 1.4 Implement `AccumulatorBehavior` — `initial_value` returns `defn.initial or 0`, `write` adds (or overwrites for unknown reduce_fn), `is_evictable` returns `False`, `resolve_conflict` sums values
- [x] 1.5 Add unit tests for all 3 built-in behaviors (12 scenarios from spec)

## 2. ChannelTypeRegistry

- [x] 2.1 Create module-level registry in `src/hecate/engine/channel.py` — `_REGISTRY: dict[str, ChannelBehavior]` with `register()`, `get()`, `list_types()` functions
- [x] 2.2 Pre-register "last_value" → `LastValueBehavior()`, "topic" → `TopicBehavior()`, "accumulator" → `AccumulatorBehavior()`, "persistent_topic" → `TopicBehavior()` at module import time
- [x] 2.3 Add unit tests: pre-registered types present, get returns correct behavior, get unknown raises KeyError, register custom type works, "persistent_topic" maps to TopicBehavior

## 3. Channel Delegation

- [x] 3.1 Refactor `Channel._initial_value()` to call `get(self.defn.type).initial_value(self.defn)` instead of if/elif
- [x] 3.2 Refactor `Channel.write()` to call `get(self.defn.type).write(self._value, value, self.defn)` and store the result
- [x] 3.3 Refactor `ChannelManager.write()` to check `get(channel.defn.type).is_evictable()` instead of `channel.defn.type in (ChannelType.TOPIC, ChannelType.PERSISTENT_TOPIC)`
- [x] 3.4 Verify existing channel tests still pass without modification (behavior is identical)

## 4. ConflictResolver Delegation

- [x] 5.1 Change `ConflictResolver.resolve()` signature — replace `channel_type: str = "last_value"` with `behavior: ChannelBehavior | None = None`
- [x] 5.2 When `behavior` is provided, delegate to `behavior.resolve_conflict(current, proposed)`; when None, fall back to last-write-wins
- [x] 5.3 Update `PregelRuntime._apply_writes()` to look up behavior from registry and pass it to `ConflictResolver.resolve()`
- [x] 5.4 Update tests in `tests/test_engine/test_temporal/test_conflict.py` to pass behavior objects instead of strings
- [x] 5.5 Add test for custom behavior conflict resolution and fallback on exception

## 5. ChannelDef Persistent Flag

- [x] 4.1 Add `persistent: bool = False` field to `ChannelDef` dataclass in `src/hecate/engine/types.py`
- [x] 4.2 Update `parse_graph()` in `src/hecate/engine/graph_dsl.py` to read `persistent` from JSON and auto-migrate `"persistent_topic"` to `ChannelType.TOPIC` + `persistent=True` with deprecation warning
- [x] 4.3 Update `schemas/graph-dsl.schema.json` — add `"persistent": { "type": "boolean" }` property to channel definition; keep `"persistent_topic"` in the type enum for backward compatibility
- [x] 4.4 Update `CompiledGraph.to_json()` to include `"persistent"` in serialized channel definitions

## 6. Template Updates

- [x] 6.1 Update `src/hecate/engine/templates.py` — replace all `ChannelType.PERSISTENT_TOPIC` with `ChannelType.TOPIC, persistent=True` (currently 0 occurrences; PERSISTENT_TOPIC is not used in templates, but verify)
- [x] 6.2 Search and update any other files referencing `ChannelType.PERSISTENT_TOPIC` to use `ChannelType.TOPIC` + `persistent=True`

## 7. Test Updates

- [x] 7.1 Update `tests/test_engine/test_pregel.py` — any `ChannelType.PERSISTENT_TOPIC` references to `ChannelType.TOPIC` + `persistent=True`
- [x] 7.2 Update `tests/test_engine/test_eviction.py` — verify eviction still works via `is_evictable()` delegation
- [x] 7.3 Update `tests/test_engine/test_graph_dsl.py` — add tests for `persistent` field parsing, deprecated `persistent_topic` migration, and custom registered type parsing
- [x] 7.4 Verify all 1129 existing tests pass with no regressions

## 8. Verification

- [x] 8.1 Run `ruff check src/hecate/ tests/`
- [x] 8.2 Run `ruff format --check src/ tests/`
- [x] 8.3 Run `mypy src/`
- [x] 8.4 Run `python -m pytest tests/ -q` — no regressions
