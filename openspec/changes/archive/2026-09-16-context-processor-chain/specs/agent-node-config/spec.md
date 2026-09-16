## ADDED Requirements

### Requirement: Context processor chain configuration per node

Agent node configuration SHALL accept a context processor chain policy: the ordered list of processors (type plus parameters), the warn threshold, `tool_result_limit`, the offload threshold, and the token budget. The policy SHALL be validated at load time: unknown processor types, unknown fields, and mutually exclusive parameters SHALL be rejected with an error identifying the invalid field. When a node does not configure a chain policy, the node SHALL inherit the agent-level policy, and failing that the platform default chain. The fully resolved policy SHALL expose a stable canonical hash for versioning and audit purposes.

#### Scenario: Node-level processor list takes effect

- **WHEN** a node configures `context_processors` with an explicit ordered processor list
- **THEN** invocations of that node SHALL apply exactly the configured processors in the configured order

#### Scenario: Invalid processor type rejected at load

- **WHEN** a node configures a processor type that is not registered
- **THEN** loading the workflow SHALL fail immediately with an error naming the unknown processor type

#### Scenario: Node without chain config inherits defaults

- **WHEN** a node does not configure any chain policy and the agent has no agent-level policy
- **THEN** the platform default chain SHALL be applied to that node's invocations

#### Scenario: Resolved policy exposes canonical hash

- **WHEN** a workflow with chain policies is loaded
- **THEN** the resolved policy per node SHALL expose a canonical hash
- **AND** re-loading the same configuration SHALL produce the same hash
