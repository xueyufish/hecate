## ADDED Requirements

### Requirement: Citation provenance configuration per node

Agent node configuration SHALL accept a citation provenance policy: an enable flag, the chunk granularity, and the minimum size threshold for marking. The policy SHALL be validated at load time: unknown fields and invalid values SHALL be rejected with an error identifying the invalid field, mirroring the context processor chain policy semantics. When a node does not configure a citation provenance policy, the node SHALL default to disabled. The fully resolved policy SHALL contribute to the node's canonical policy hash so enabling or re-parameterizing provenance is visible in agent versioning.

#### Scenario: Node enables citation provenance

- **WHEN** a node configures citation provenance with explicit granularity and threshold
- **THEN** tool results written by that node's invocations SHALL be chunked and marked per the configured policy

#### Scenario: Default is disabled

- **WHEN** a node does not configure a citation provenance policy
- **THEN** tool results SHALL pass through unmarked and no citation events SHALL be emitted for that node

#### Scenario: Invalid citation configuration rejected at load

- **WHEN** a node configures a citation granularity of zero or a negative threshold
- **THEN** loading the workflow SHALL fail immediately with an error identifying the invalid field

#### Scenario: Resolved citation policy is versioned

- **WHEN** two nodes resolve to the same effective citation provenance policy
- **THEN** their resolved policies SHALL produce identical canonical hash contributions
