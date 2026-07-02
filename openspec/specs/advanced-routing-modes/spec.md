## ADDED Requirements

### Requirement: Routing mode field on CONDITION node config
The CONDITION node config SHALL support an optional `routing_mode` field with values `"condition"` (default), `"intent"`, and `"dynamic"`. When `routing_mode` is absent or `"condition"`, behavior is identical to existing expression-based routing.

#### Scenario: Default routing mode is condition
- **WHEN** a CONDITION node has no `routing_mode` field
- **THEN** the node SHALL use expression-based routing, identical to current behavior

#### Scenario: Explicit condition mode
- **WHEN** a CONDITION node has `routing_mode: "condition"`
- **THEN** the node SHALL evaluate the `expression` field and write the result to `_route` channel

#### Scenario: Invalid routing mode rejected
- **WHEN** a CONDITION node has `routing_mode: "unknown"`
- **THEN** `parse_graph()` SHALL raise `GraphValidationError` with field indicating the invalid routing mode

### Requirement: Intent-based routing mode
When `routing_mode: "intent"`, the CONDITION node SHALL route based on intent classification. The `routing_config` field SHALL contain `intent_patterns` (a list of `{pattern: str, target: str}` objects) and an optional `routing_prompt` (string). The engine SHALL first attempt regex pattern matching against the input channel value. If no pattern matches and `routing_prompt` is provided, the engine SHALL call `EnginePort.llm_invoke()` to classify the intent. The `_route` value SHALL be set to the matched target.

#### Scenario: Intent pattern match
- **WHEN** a CONDITION node has `routing_mode: "intent"` and `intent_patterns: [{pattern: "billing|invoice", target: "billing_agent"}, {pattern: "technical|bug", target: "tech_support"}]`
- **AND** the input channel value contains "I have a billing question"
- **THEN** the `_route` value SHALL be "billing_agent"

#### Scenario: Intent pattern no match with LLM fallback
- **WHEN** a CONDITION node has `routing_mode: "intent"`, `intent_patterns: [{pattern: "billing", target: "billing_agent"}]`, and `routing_prompt: "Classify the user intent into one of: billing, technical, general"`
- **AND** the input channel value contains "How do I reset my password?"
- **AND** no pattern matches
- **THEN** the engine SHALL call `EnginePort.llm_invoke()` with the routing prompt and input
- **AND** the LLM response SHALL be used to determine the `_route` value

#### Scenario: Intent pattern no match without LLM fallback
- **WHEN** a CONDITION node has `routing_mode: "intent"` and `intent_patterns: [{pattern: "billing", target: "billing_agent"}]`
- **AND** the input channel value contains "Hello, how are you?"
- **AND** no `routing_prompt` is provided
- **THEN** the `_route` value SHALL be set to the "default" key from the edge target dict

#### Scenario: Intent routing config validation
- **WHEN** a CONDITION node has `routing_mode: "intent"` but no `routing_config.intent_patterns`
- **THEN** the compiler SHALL raise `GraphValidationError` with message indicating intent routing requires intent_patterns

### Requirement: Dynamic routing mode
When `routing_mode: "dynamic"`, the CONDITION node SHALL call `EnginePort.llm_invoke()` to select the next speaker from a list of `candidate_agents`. The `routing_config` SHALL contain `candidate_agents` (list of node IDs), `routing_prompt` (string), and an optional `allow_repeated_speaker` (boolean, default false). The LLM response SHALL be validated against the `candidate_agents` list.

#### Scenario: Dynamic routing selects valid agent
- **WHEN** a CONDITION node has `routing_mode: "dynamic"`, `candidate_agents: ["agent_a", "agent_b", "agent_c"]`, and `routing_prompt: "Select the best agent to respond"`
- **THEN** the engine SHALL call `EnginePort.llm_invoke()` with the routing prompt, available channel state, and candidate list
- **AND** if the LLM returns "agent_b", the `_route` value SHALL be "agent_b"

#### Scenario: Dynamic routing invalid response falls back to default
- **WHEN** a CONDITION node has `routing_mode: "dynamic"`, `candidate_agents: ["agent_a", "agent_b"]`
- **AND** the LLM returns "unknown_agent" which is not in the candidate list
- **THEN** the `_route` value SHALL be set to the "default" key from the edge target dict

#### Scenario: Dynamic routing allow_repeated_speaker false
- **WHEN** a CONDITION node has `routing_mode: "dynamic"`, `allow_repeated_speaker: false`, and the last speaker was "agent_a"
- **THEN** "agent_a" SHALL be excluded from the candidate list sent to the LLM

#### Scenario: Dynamic routing config validation
- **WHEN** a CONDITION node has `routing_mode: "dynamic"` but no `routing_config.candidate_agents`
- **THEN** the compiler SHALL raise `GraphValidationError` with message indicating dynamic routing requires candidate_agents

#### Scenario: Dynamic routing candidate must reference existing agent nodes
- **WHEN** a CONDITION node has `routing_mode: "dynamic"` and `candidate_agents: ["agent_a", "nonexistent"]`
- **THEN** the compiler SHALL raise `GraphValidationError` indicating candidate "nonexistent" is not a declared node

### Requirement: Routing config schema in Graph DSL
The `routing_config` object in CONDITION node config SHALL conform to a discriminated schema based on `routing_mode`. The Graph DSL JSON Schema SHALL be updated to include `routing_mode`, `routing_config` with `intent_patterns`, `candidate_agents`, `routing_prompt`, and `allow_repeated_speaker` fields.

#### Scenario: Intent routing config in DSL
- **WHEN** `parse_graph()` receives a CONDITION node with `routing_mode: "intent"` and `routing_config: {intent_patterns: [{pattern: "sales", target: "sales_agent"}], routing_prompt: "Classify intent"}`
- **THEN** the parsed `NodeConfig` SHALL contain `routing_mode="intent"` and the routing_config values

#### Scenario: Dynamic routing config in DSL
- **WHEN** `parse_graph()` receives a CONDITION node with `routing_mode: "dynamic"` and `routing_config: {candidate_agents: ["a", "b"], routing_prompt: "Pick best agent", allow_repeated_speaker: true}`
- **THEN** the parsed `NodeConfig` SHALL contain `routing_mode="dynamic"` and the routing_config values
