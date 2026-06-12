## MODIFIED Requirements

### Requirement: Factory functions exported from templates module
The system SHALL export `build_sequential_pipeline`, `build_broadcast_pipeline`, `build_negotiation_graph`, and `build_debate_graph` from `engine/templates.py`. Additionally, `build_negotiation_graph` and `build_debate_graph` SHALL have corresponding JSON template files for catalog listing.

#### Scenario: Import negotiation graph factory
- **WHEN** `from hecate.engine.templates import build_negotiation_graph` is executed
- **THEN** the import SHALL succeed and the function SHALL be callable

#### Scenario: Import debate graph factory
- **WHEN** `from hecate.engine.templates import build_debate_graph` is executed
- **THEN** the import SHALL succeed and the function SHALL be callable

## ADDED Requirements

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
