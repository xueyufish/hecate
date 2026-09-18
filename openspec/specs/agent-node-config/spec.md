## Purpose

Defines the agent node configuration surface: the structured form fields an
operator sets per workflow node (role description mapped to the system
prompt, invocation mode, readable/writable channels, model override) and the
per-node policy sections validated at load time and folded into the node's
canonical policy hash — context processor chain (4.13), citation provenance
(1.3.5e Stage 1), and grounding scoring (1.3.5e Stage 2).

## Requirements

### Requirement: Agent node config panel with structured form
The agent node config panel SHALL display a structured form with the following fields: agent selector (dropdown), role description (textarea), invocation mode (radio), channel selector (dual-list), and model override (text input).

#### Scenario: Agent node config panel renders all fields
- **WHEN** the user clicks an agent node on the canvas
- **THEN** the config panel displays: agent selector dropdown, role description textarea, invocation mode radio (direct/tool), channel selector with readable/writable lists, and model override text input

#### Scenario: Agent selector populates from API
- **WHEN** the user opens the agent node config panel
- **THEN** the agent selector dropdown SHALL fetch available agents from `/api/agents` and display them by name

#### Scenario: Agent selector sets agent_ref
- **WHEN** the user selects an agent from the dropdown
- **THEN** the node's `config.agent_ref` SHALL be set to the selected agent's ID

### Requirement: Role description field maps to system_prompt
The agent node config panel SHALL include a role description textarea that maps to the node's `config.system_prompt` field.

#### Scenario: Role description updates system_prompt
- **WHEN** the user enters "You are a research analyst" in the role description field
- **THEN** the node's `config.system_prompt` SHALL be updated to "You are a research analyst"

### Requirement: Invocation mode selector
The agent node config panel SHALL include an invocation mode radio selector with options "Direct" and "Tool", mapping to `config.invocation_mode` values "direct" and "tool" respectively.

#### Scenario: Select direct invocation mode
- **WHEN** the user selects "Direct" in the invocation mode radio
- **THEN** the node's `config.invocation_mode` SHALL be set to "direct"

#### Scenario: Select tool invocation mode
- **WHEN** the user selects "Tool" in the invocation mode radio
- **THEN** the node's `config.invocation_mode` SHALL be set to "tool"

### Requirement: Channel selector with readable/writable dual-list
The agent node config panel SHALL include a channel selector component that reads available channels from the graph's `state` declaration and allows the user to assign channels as readable, writable, both, or neither for the agent.

#### Scenario: Channel selector shows graph state channels
- **WHEN** the user opens the channel selector for an agent node in a graph with state channels "messages", "research_data", and "analysis_results"
- **THEN** the selector SHALL display all three channels as options

#### Scenario: Assign readable channels
- **WHEN** the user moves "messages" and "research_data" to the readable list
- **THEN** the node's `config.channels.readable` SHALL be set to ["messages", "research_data"]

#### Scenario: Assign writable channels
- **WHEN** the user moves "messages" and "analysis_results" to the writable list
- **THEN** the node's `config.channels.writable` SHALL be set to ["messages", "analysis_results"]

#### Scenario: Empty graph shows channel hint
- **WHEN** the graph has no state channels declared
- **THEN** the channel selector SHALL display a hint "Add channels in graph settings" and allow freeform channel name entry

### Requirement: Model override field
The agent node config panel SHALL include a model override text input that sets `config.model` for the agent node.

#### Scenario: Model override sets config.model
- **WHEN** the user enters "gpt-4o-mini" in the model override field
- **THEN** the node's `config.model` SHALL be set to "gpt-4o-mini"

#### Scenario: Empty model override removes override
- **WHEN** the user clears the model override field
- **THEN** the node's `config.model` SHALL be removed from the config

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

### Requirement: Grounding scoring configuration per node

Agent node configuration SHALL accept a grounding scoring policy: an enable flag (default disabled), the scoring backend selection (`llm_judge` or an HTTP NLI endpoint definition), the trigger condition (`on_uncited` default, `always`, or a sample rate), disposition threshold fields used for the shadow disposition, a fallback retrieval section (enable flag plus dedicated fallback knowledge base IDs, independent of the node's retrieval `kb_ids`), and escalation settings for upgrading borderline pairs to the judge backend. The policy SHALL be validated at load time: unknown fields and invalid values SHALL be rejected with an error identifying the invalid field, mirroring the citation provenance policy semantics. The fully resolved policy SHALL contribute to the node's canonical policy hash.

#### Scenario: Node enables scoring with LLM judge backend

- **WHEN** a node configures grounding scoring with `backend: llm_judge` and the default trigger
- **THEN** responses of that node SHALL be scored per the grounding scoring behavior, with verdicts recorded in audit events

#### Scenario: Node configures HTTP NLI endpoint with fallback

- **WHEN** a node selects the HTTP NLI backend with an endpoint definition and enables fallback retrieval with explicit knowledge base IDs
- **THEN** scoring SHALL call the configured endpoint for (claim, evidence) pairs and uncited claims SHALL be scored against retrieval from the fallback knowledge bases

#### Scenario: Unknown field rejected

- **WHEN** a grounding scoring policy contains a field outside the supported set
- **THEN** configuration SHALL fail at load time with an error naming the unknown field

#### Scenario: Default is disabled

- **WHEN** a node does not configure grounding scoring
- **THEN** no scoring, backend calls, or scoring events SHALL occur for that node, and behavior SHALL be identical to a platform without the feature
