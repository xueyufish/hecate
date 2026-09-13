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

When `routing_mode: "intent"`, the CONDITION node SHALL route based on intent classification. The `routing_config` field SHALL support two forms. Legacy form: `intent_patterns` (a list of `{pattern: str, target: str}` objects) with an optional `routing_prompt` (string) — the engine SHALL first attempt regex pattern matching against the input channel value, and if no pattern matches and `routing_prompt` is provided, SHALL call LLM classification with the routing prompt; the `_route` value SHALL be set to the matched target. Package-backed form: an `intent_package` reference (package id, optionally with a pinned published version, defaulting to the latest published version) together with a `category_targets` map from package categories to route keys — the node SHALL delegate classification to the intent recognition engine, using the package's published evidence, decision cache, and layered result; the `_route` value SHALL be the target mapped from the recognized category, or the "default" edge key when no category matches. Graph validation SHALL reject a package-backed configuration whose package reference is unresolvable or whose `category_targets` keys reference categories absent from the referenced package version. At recognition time, if evidence cannot be resolved, the node SHALL fall back to LLM classification over `category_targets` keys and record the degradation in the recognition event. When `routing_config` contains neither `intent_package` nor `intent_patterns`, the compiler SHALL raise `GraphValidationError`.

#### Scenario: Legacy pattern match unchanged

- **WHEN** a CONDITION node has `routing_mode: "intent"` and `intent_patterns: [{pattern: "billing|invoice", target: "billing_agent"}, {pattern: "technical|bug", target: "tech_support"}]`
- **AND** the input channel value contains "I have a billing question"
- **THEN** the `_route` value SHALL be "billing_agent" with behavior identical to the pre-existing legacy form

#### Scenario: Legacy no-match with LLM fallback unchanged

- **WHEN** a CONDITION node has `routing_mode: "intent"`, `intent_patterns` that do not match the input, and a `routing_prompt`
- **THEN** the engine SHALL call LLM classification with the routing prompt and input, and the response SHALL determine the `_route` value

#### Scenario: Package-backed delegation uses engine evidence and cache

- **WHEN** a CONDITION node has `routing_mode: "intent"` with `intent_package` referencing a published package and `category_targets: {billing: "billing_agent", technical: "tech_support"}`
- **AND** the input matches a package category after a cache miss
- **THEN** the `_route` value SHALL be the mapped target, the recognition event SHALL cite the package version and decision source, and a repeated identical utterance SHALL hit the decision cache without another LLM call

#### Scenario: Package-backed no match falls back to default

- **WHEN** the recognized category matches no key in `category_targets`
- **THEN** the `_route` value SHALL be set to the "default" key from the edge target dict

#### Scenario: Unresolvable package reference rejected at validation

- **WHEN** a CONDITION node config references a package id that does not exist, or a version pin that is not published
- **THEN** the compiler SHALL raise `GraphValidationError` identifying the unresolvable package reference

#### Scenario: Category key absent from package rejected at validation

- **WHEN** `category_targets` contains a key that is not a category in the referenced package version
- **THEN** the compiler SHALL raise `GraphValidationError` naming the unknown category

#### Scenario: Evidence failure degrades to LLM classification

- **WHEN** the evidence port fails while resolving the referenced package at recognition time
- **THEN** the node SHALL classify via LLM over the `category_targets` keys, route accordingly, and the recognition event SHALL record the evidence degradation

#### Scenario: Config without patterns or package rejected

- **WHEN** a CONDITION node has `routing_mode: "intent"` but `routing_config` contains neither `intent_patterns` nor `intent_package`
- **THEN** the compiler SHALL raise `GraphValidationError` indicating intent routing requires intent_patterns or an intent_package reference

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
