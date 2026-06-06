## MODIFIED Requirements

### Requirement: PregelRuntime accepts optional eviction policy
PregelRuntime SHALL accept an optional `eviction_policy: EvictionPolicy | None = None` constructor parameter. When None, it SHALL default to `NoEviction()`. The eviction policy SHALL be passed through to the ChannelManager constructor.

#### Scenario: Default eviction policy
- **WHEN** PregelRuntime is created without eviction_policy
- **THEN** the internal ChannelManager SHALL use `NoEviction()`

#### Scenario: Custom eviction policy
- **WHEN** PregelRuntime is created with `eviction_policy=SizeBasedEviction(max_size=100)`
- **THEN** the internal ChannelManager SHALL use the provided eviction policy for all TOPIC channel writes
