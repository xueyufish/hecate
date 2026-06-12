## Purpose

Orchestration templates provide pre-built agent workflow graph configurations for common collaboration patterns. Templates are auto-discovered from JSON files in `data/orchestration_templates/` and available via the API and canvas UI.
## Requirements
### Requirement: Fan-out Pipeline template
The system SHALL include a pre-built "Fan-out Pipeline" orchestration template demonstrating parallel processing with a researcher agent fanning out to multiple analyst agents and merging results.

#### Scenario: Fan-out template structure
- **WHEN** the Fan-out Pipeline template is loaded
- **THEN** the graph SHALL contain 1 researcher AGENT node, 1 FAN_OUT node, 3 analyst AGENT nodes (analyst_a, analyst_b, analyst_c), 1 MERGE node, and 1 summarizer AGENT node

#### Scenario: Fan-out template edges
- **WHEN** the template is compiled
- **THEN** edges SHALL be: researcher→fanout, fanout→[analyst_a, analyst_b, analyst_c], analyst_*→merge, merge→summarizer, summarizer→__end__

### Requirement: Conditional Pipeline template
The system SHALL include a pre-built "Conditional Pipeline" orchestration template demonstrating multi-key conditional routing based on classification.

#### Scenario: Conditional template structure
- **WHEN** the Conditional Pipeline template is loaded
- **THEN** the graph SHALL contain 1 classifier AGENT node, 1 CONDITION node, and 3 specialist AGENT nodes (finance_agent, tech_agent, legal_agent) with multi-key conditional edge routing

#### Scenario: Conditional template routing
- **WHEN** the classifier agent outputs a category
- **THEN** the CONDITION node SHALL route to the matching specialist based on the category value

### Requirement: Reflection Loop template
The system SHALL include a pre-built "Reflection Loop" orchestration template demonstrating iterative refinement with a quality check loop.

#### Scenario: Reflection template structure
- **WHEN** the Reflection Loop template is loaded
- **THEN** the graph SHALL contain 1 drafter AGENT node, 1 reviewer AGENT node, 1 CONDITION node, and 1 reviser AGENT node with a loop edge from reviser back to reviewer

#### Scenario: Reflection loop iteration
- **WHEN** the reviewer determines quality is insufficient
- **THEN** the CONDITION node SHALL route to the reviser, which then routes back to the reviewer for re-evaluation

#### Scenario: Reflection loop termination
- **WHEN** the reviewer determines quality is approved
- **THEN** the CONDITION node SHALL route to __end__

### Requirement: Sequential Pipeline template in catalog
The system SHALL include a "Sequential Pipeline" orchestration template in the template catalog, auto-discovered from `data/orchestration_templates/sequential-pipeline.json`.

#### Scenario: Sequential template listed in API
- **WHEN** the `/api/orchestration-templates` endpoint is called
- **THEN** the response SHALL include an entry with `id` "sequential-pipeline" containing name, description, category, and preview metadata

### Requirement: Broadcast Pipeline template in catalog
The system SHALL include a "Broadcast Pipeline" orchestration template in the template catalog, auto-discovered from `data/orchestration_templates/broadcast-pipeline.json`.

#### Scenario: Broadcast template listed in API
- **WHEN** the `/api/orchestration-templates` endpoint is called
- **THEN** the response SHALL include an entry with `id` "broadcast-pipeline" containing name, description, category, and preview metadata

### Requirement: Factory functions exported from templates module
The system SHALL export `build_sequential_pipeline`, `build_broadcast_pipeline`, `build_negotiation_graph`, and `build_debate_graph` from `engine/templates.py`. Additionally, `build_negotiation_graph` and `build_debate_graph` SHALL have corresponding JSON template files for catalog listing.

#### Scenario: Import negotiation graph factory
- **WHEN** `from hecate.engine.templates import build_negotiation_graph` is executed
- **THEN** the import SHALL succeed and the function SHALL be callable

#### Scenario: Import debate graph factory
- **WHEN** `from hecate.engine.templates import build_debate_graph` is executed
- **THEN** the import SHALL succeed and the function SHALL be callable

### Requirement: Orchestration template listing includes pattern type
The `GET /api/orchestration-templates` endpoint SHALL include a `pattern_type` field in each template item, inferred from the template's graph structure using `infer_pattern()`.

#### Scenario: Sequential template has pattern_type
- **WHEN** `GET /api/orchestration-templates` is called
- **THEN** the `sequential-pipeline` item SHALL have `pattern_type` set to `"sequential"`

#### Scenario: Fan-out template has pattern_type
- **WHEN** `GET /api/orchestration-templates` is called
- **THEN** the `fan-out-pipeline` item SHALL have `pattern_type` set to `"parallel"`

#### Scenario: Customer service template has pattern_type
- **WHEN** `GET /api/orchestration-templates` is called
- **THEN** the `customer-service-triage` item SHALL have `pattern_type` set to `"handoff"`

#### Scenario: Broadcast template has pattern_type
- **WHEN** `GET /api/orchestration-templates` is called
- **THEN** the `broadcast-pipeline` item SHALL have `pattern_type` set to `"broadcast"`

#### Scenario: Template with no matching pattern
- **WHEN** a template's graph structure does not match any known pattern
- **THEN** its `pattern_type` SHALL be `null`

