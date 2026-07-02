## MODIFIED Requirements

### Requirement: ChannelDef includes persistence flag
The `ChannelDef` dataclass SHALL include a `persistent: bool = False` field. The `ChannelType` enum SHALL retain `PERSISTENT_TOPIC` for backward compatibility but the registry SHALL map it to `TopicBehavior`.

#### Scenario: Default non-persistent
- **WHEN** `ChannelDef(type=ChannelType.TOPIC)` is created
- **THEN** `persistent` SHALL be `False`

#### Scenario: Explicit persistent
- **WHEN** `ChannelDef(type=ChannelType.TOPIC, persistent=True)` is created
- **THEN** `persistent` SHALL be `True`

#### Scenario: PERSISTENT_TOPIC auto-migration
- **WHEN** `parse_graph()` encounters `"type": "persistent_topic"` in a graph definition
- **THEN** it SHALL create `ChannelDef(type=ChannelType.TOPIC, persistent=True)` and log a deprecation warning
