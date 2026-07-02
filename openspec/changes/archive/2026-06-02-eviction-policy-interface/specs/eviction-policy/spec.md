## ADDED Requirements

### Requirement: EvictionPolicy ABC defines pluggable channel eviction
The engine SHALL define an `EvictionPolicy` ABC in `engine/eviction.py` with methods: `should_evict` and `select_victim`.

#### Scenario: Check if eviction is needed
- **WHEN** `should_evict(channel_name, current_size, context)` is called
- **THEN** it SHALL return `True` if eviction should occur, `False` otherwise

#### Scenario: Select items to keep
- **WHEN** `select_victim(items, max_count)` is called with a list of items and max count
- **THEN** it SHALL return a list of items to keep (evict the rest)

### Requirement: NoEviction preserves current unbounded behavior
A `NoEviction` SHALL implement EvictionPolicy by never evicting (always returns False).

#### Scenario: NoEviction never evicts
- **WHEN** `should_evict("messages", 10000, {})` is called on NoEviction
- **THEN** it SHALL return `False`

#### Scenario: NoEviction keeps all items
- **WHEN** `select_victim(items, 5)` is called on NoEviction
- **THEN** it SHALL return all items unchanged

### Requirement: SizeBasedEviction evicts oldest when size exceeds max
A `SizeBasedEviction` SHALL implement EvictionPolicy by evicting oldest items when channel size exceeds a configured maximum.

#### Scenario: Below max size
- **WHEN** `should_evict("messages", 50, {})` is called with max_size=100
- **THEN** it SHALL return `False`

#### Scenario: At max size
- **WHEN** `should_evict("messages", 100, {})` is called with max_size=100
- **THEN** it SHALL return `True`

#### Scenario: Above max size
- **WHEN** `should_evict("messages", 150, {})` is called with max_size=100
- **THEN** it SHALL return `True`

#### Scenario: Keep newest items
- **WHEN** `select_victim(["a", "b", "c", "d", "e"], 3)` is called
- **THEN** it SHALL return `["c", "d", "e"]` (keep the last 3)

### Requirement: ChannelManager accepts optional eviction policy
ChannelManager SHALL accept an optional `eviction_policy` parameter, defaulting to `NoEviction()`.

#### Scenario: Default eviction policy
- **WHEN** ChannelManager is created without eviction_policy
- **THEN** it SHALL use `NoEviction()` internally

#### Scenario: Custom eviction policy
- **WHEN** ChannelManager is created with SizeBasedEviction(max_size=100)
- **THEN** it SHALL apply eviction on TOPIC channel writes when size exceeds 100
