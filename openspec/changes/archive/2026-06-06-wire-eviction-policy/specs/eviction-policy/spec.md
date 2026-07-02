## MODIFIED Requirements

### Requirement: ChannelManager accepts optional eviction policy
ChannelManager SHALL accept an optional `eviction_policy` parameter in its constructor, defaulting to `NoEviction()`. After each write to a TOPIC or PERSISTENT_TOPIC channel, ChannelManager SHALL call `eviction_policy.should_evict()`. If eviction is needed, ChannelManager SHALL replace the channel's value with the result of `eviction_policy.select_victim()`.

#### Scenario: Default eviction policy
- **WHEN** ChannelManager is created without eviction_policy
- **THEN** it SHALL use `NoEviction()` internally and never evict

#### Scenario: Custom eviction policy with TOPIC channel
- **WHEN** ChannelManager is created with `SizeBasedEviction(max_size=3)` and a TOPIC channel "messages" has 4 items
- **THEN** after writing a 5th item, ChannelManager SHALL call should_evict("messages", 5, {}) which returns True
- **AND** ChannelManager SHALL call select_victim([all 5 items]) which returns the 3 newest items

#### Scenario: LAST_VALUE channel is not affected by eviction
- **WHEN** ChannelManager is created with `SizeBasedEviction(max_size=3)` and writes to a LAST_VALUE channel
- **THEN** eviction SHALL NOT be applied (only TOPIC and PERSISTENT_TOPIC channels trigger eviction checks)

#### Scenario: ACCUMULATOR channel is not affected by eviction
- **WHEN** ChannelManager is created with `SizeBasedEviction(max_size=3)` and writes to an ACCUMULATOR channel
- **THEN** eviction SHALL NOT be applied

#### Scenario: Restore does not trigger eviction
- **WHEN** ChannelManager.restore(state) is called with state containing a TOPIC channel with 10 items
- **THEN** the channel SHALL receive all 10 items without any eviction, regardless of the eviction policy
