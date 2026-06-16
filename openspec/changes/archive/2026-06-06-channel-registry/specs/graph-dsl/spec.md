## MODIFIED Requirements

### Requirement: Graph DSL parser validates against JSON Schema
The `parse_graph()` function SHALL accept a JSON string or dict and validate it against `schemas/graph-dsl.schema.json`. The schema SHALL include `"persistent"` as an optional boolean property on channel definitions. The parser SHALL auto-migrate deprecated `"persistent_topic"` to `"topic"` with `persistent=True`.

#### Scenario: Persistent channel in JSON
- **WHEN** `parse_graph()` encounters a channel definition with `"type": "topic", "persistent": true`
- **THEN** it SHALL create `ChannelDef(type=ChannelType.TOPIC, persistent=True)`

#### Scenario: Deprecated persistent_topic
- **WHEN** `parse_graph()` encounters `"type": "persistent_topic"`
- **THEN** it SHALL create `ChannelDef(type=ChannelType.TOPIC, persistent=True)` and log a deprecation warning

#### Scenario: Custom registered type
- **WHEN** `parse_graph()` encounters `"type": "priority_queue"` and "priority_queue" is registered in ChannelTypeRegistry
- **THEN** it SHALL create `ChannelDef(type=ChannelType("priority_queue"))` without error

#### Scenario: Unknown type
- **WHEN** `parse_graph()` encounters `"type": "unknown"` and "unknown" is NOT in the registry
- **THEN** it SHALL raise `GraphValidationError` with field pointing to the channel type
