## ADDED Requirements

### Requirement: Multi-key conditional routing
The CONDITION node SHALL support routing to more than two branches by allowing the edge target dict to contain arbitrary string keys beyond "true" and "false". The `_route` value written by the CONDITION node's expression evaluation SHALL be used as the lookup key in the edge target dict.

#### Scenario: Multi-key routing with three branches
- **WHEN** a CONDITION node evaluates expression and writes `_route: "high"`
- **AND** the outgoing edge target is `{"high": "priority_handler", "medium": "standard_handler", "low": "batch_handler"}`
- **THEN** execution SHALL route to "priority_handler"

#### Scenario: Fallback to default key
- **WHEN** a CONDITION node evaluates expression and writes `_route: "unknown"`
- **AND** the edge target dict has a "default" key
- **THEN** execution SHALL route to the node specified by the "default" key

#### Scenario: Backward compatibility with true/false routing
- **WHEN** a CONDITION node evaluates expression and writes `_route: "true"`
- **AND** the edge target is `{"true": "node_a", "false": "node_b"}`
- **THEN** execution SHALL route to "node_a" — identical to current behavior

#### Scenario: Legacy false fallback preserved
- **WHEN** a CONDITION node evaluates expression and writes `_route: "unknown"`
- **AND** the edge target dict has no "default" key but has a "false" key
- **THEN** execution SHALL fall back to the "false" key for backward compatibility

### Requirement: Condition expression evaluation produces route key
The CONDITION node's `expression` config field SHALL support comparison expressions that produce a string route key, not just a boolean. Supported expressions SHALL include: equality (`field == value`), greater-than (`field > threshold`), less-than (`field < threshold`), and direct field reference.

#### Scenario: Equality expression produces route key
- **WHEN** a CONDITION node has expression `category` and channel value for "category" is "finance"
- **THEN** the `_route` value SHALL be "finance"

#### Scenario: Threshold expression produces route key
- **WHEN** a CONDITION node has expression `score > 80 ? "high" : "low"` and channel value for "score" is 90
- **THEN** the `_route` value SHALL be "high"
