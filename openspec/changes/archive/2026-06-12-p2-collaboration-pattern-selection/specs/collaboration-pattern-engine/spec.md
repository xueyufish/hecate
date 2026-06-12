## ADDED Requirements

### Requirement: CollaborationPattern enum
The engine SHALL define a `CollaborationPattern` StrEnum in `engine/patterns.py` with 6 values: `SEQUENTIAL`, `PARALLEL`, `HANDOFF`, `BROADCAST`, `NEGOTIATION`, `DEBATE`. Each value maps to a canonical collaboration topology.

#### Scenario: Enum values are accessible
- **WHEN** `from hecate.engine.patterns import CollaborationPattern` is executed
- **THEN** the enum SHALL have exactly 6 members: SEQUENTIAL, PARALLEL, HANDOFF, BROADCAST, NEGOTIATION, DEBATE

#### Scenario: Enum values are lowercase strings
- **WHEN** `CollaborationPattern.SEQUENTIAL.value` is accessed
- **THEN** it SHALL return `"sequential"`

### Requirement: Pattern inference from graph structure
The system SHALL provide an `infer_pattern(config: GraphConfig) -> CollaborationPattern | None` function that analyzes graph topology to detect the collaboration pattern. The function returns `None` if no known pattern matches.

#### Scenario: Sequential pattern detected
- **WHEN** `infer_pattern()` receives a GraphConfig with a linear chain of 3+ AGENT nodes connected by unconditional edges, no FAN_OUT/MERGE nodes, and no handoff edges
- **THEN** it SHALL return `CollaborationPattern.SEQUENTIAL`

#### Scenario: Parallel pattern detected
- **WHEN** `infer_pattern()` receives a GraphConfig containing at least one FAN_OUT node and one MERGE node
- **THEN** it SHALL return `CollaborationPattern.PARALLEL`

#### Scenario: Handoff pattern detected
- **WHEN** `infer_pattern()` receives a GraphConfig where all edges have `trigger="handoff"`
- **THEN** it SHALL return `CollaborationPattern.HANDOFF`

#### Scenario: Broadcast pattern detected
- **WHEN** `infer_pattern()` receives a GraphConfig where all agent nodes share read/write access to a single TOPIC channel and edges form a sequential chain without FAN_OUT/MERGE
- **THEN** it SHALL return `CollaborationPattern.BROADCAST`

#### Scenario: Negotiation pattern detected
- **WHEN** `infer_pattern()` receives a GraphConfig with exactly 2 agent nodes, a condition node checking agreement status, and a loop edge from the responder back to the proposer
- **THEN** it SHALL return `CollaborationPattern.NEGOTIATION`

#### Scenario: Debate pattern detected
- **WHEN** `infer_pattern()` receives a GraphConfig with 2+ agent nodes, a round counter ACCUMULATOR channel, and alternating execution edges forming a loop
- **THEN** it SHALL return `CollaborationPattern.DEBATE`

#### Scenario: Unknown pattern returns None
- **WHEN** `infer_pattern()` receives a GraphConfig that does not match any known pattern
- **THEN** it SHALL return `None`

### Requirement: Pattern-to-graph builder
The system SHALL provide a `build_graph_from_pattern(pattern: CollaborationPattern, config: dict) -> GraphConfig` function that generates a complete GraphConfig from a pattern type and configuration parameters.

#### Scenario: Build sequential graph
- **WHEN** `build_graph_from_pattern(CollaborationPattern.SEQUENTIAL, {"stages": [{"name": "researcher", "model": "gpt-4o", "prompt": "..."}]})` is called
- **THEN** it SHALL return a GraphConfig with AGENT nodes connected in a linear chain, TOPIC channel for messages, and entry point at the first stage

#### Scenario: Build parallel graph
- **WHEN** `build_graph_from_pattern(CollaborationPattern.PARALLEL, {"coordinator": {...}, "workers": [{...}, {...}], "aggregator": {...}})` is called
- **THEN** it SHALL return a GraphConfig with coordinator AGENT → FAN_OUT → worker AGENT nodes → MERGE → aggregator AGENT

#### Scenario: Build handoff graph
- **WHEN** `build_graph_from_pattern(CollaborationPattern.HANDOFF, {"router": {...}, "specialists": [{...}, {...}]})` is called
- **THEN** it SHALL return a GraphConfig with router AGENT connected to specialist AGENT nodes via handoff edges (`trigger="handoff"`)

#### Scenario: Build broadcast graph
- **WHEN** `build_graph_from_pattern(CollaborationPattern.BROADCAST, {"participants": [{...}, {...}], "moderator": {...}})` is called
- **THEN** it SHALL return a GraphConfig with all participants sharing a TOPIC channel, sequential edges between participants, and an optional moderator at the end

#### Scenario: Build negotiation graph
- **WHEN** `build_graph_from_pattern(CollaborationPattern.NEGOTIATION, {"proposer": {...}, "responder": {...}, "max_rounds": 5})` is called
- **THEN** it SHALL return a GraphConfig with proposer AGENT → responder AGENT → condition node checking agreement → loop back to proposer or end

#### Scenario: Build debate graph
- **WHEN** `build_graph_from_pattern(CollaborationPattern.DEBATE, {"debater_a": {...}, "debater_b": {...}, "judge": {...}, "rounds": 3})` is called
- **THEN** it SHALL return a GraphConfig with debater_a → debater_b alternating via a loop with round counter, and an optional judge AGENT at the end

### Requirement: Pattern metadata endpoint
The system SHALL expose `GET /api/collaboration-patterns` returning a list of all 6 pattern definitions with name, description, required parameters, and preview metadata.

#### Scenario: List all patterns
- **WHEN** `GET /api/collaboration-patterns` is called with a valid API key
- **THEN** the response SHALL contain an `items` array with exactly 6 entries, each having `id`, `name`, `description`, `parameters` (JSON Schema for configuration), and `preview` (node count, edge count estimates)

#### Scenario: Pattern parameter schemas
- **WHEN** the sequential pattern entry is inspected from the response
- **THEN** its `parameters` SHALL be a JSON Schema object describing required fields: `stages` (array of objects with `name`, `model`, `prompt`)

### Requirement: Pattern graph generation endpoint
The system SHALL expose `POST /api/collaboration-patterns/{pattern}/generate` accepting pattern configuration and returning a complete Graph DSL JSON.

#### Scenario: Generate sequential graph via API
- **WHEN** `POST /api/collaboration-patterns/sequential/generate` is called with `{"stages": [{"name": "step1", "model": "gpt-4o", "prompt": "You are step 1."}, {"name": "step2", "model": "gpt-4o", "prompt": "You are step 2."}]}`
- **THEN** the response SHALL be a valid Graph DSL JSON with 2 AGENT nodes, sequential edges, a TOPIC `messages` channel, and entry at `step1`

#### Scenario: Generate with invalid pattern
- **WHEN** `POST /api/collaboration-patterns/unknown/generate` is called
- **THEN** the API SHALL return 422 with error detail indicating the pattern is not valid

#### Scenario: Generate with missing required parameter
- **WHEN** `POST /api/collaboration-patterns/sequential/generate` is called with `{}` (missing stages)
- **THEN** the API SHALL return 422 with validation error indicating `stages` is required

### Requirement: Negotiation and debate JSON templates
The system SHALL include `negotiation.json` and `debate.json` template files in `data/orchestration_templates/`, auto-discovered by the template loading system.

#### Scenario: Negotiation template in catalog
- **WHEN** `GET /api/orchestration-templates` is called
- **THEN** the response SHALL include an entry with `id` "negotiation"

#### Scenario: Debate template in catalog
- **WHEN** `GET /api/orchestration-templates` is called
- **THEN** the response SHALL include an entry with `id` "debate"
