## ADDED Requirements

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
The system SHALL export `build_sequential_pipeline` and `build_broadcast_pipeline` from `engine/templates.py`.

#### Scenario: Import sequential pipeline factory
- **WHEN** `from hecate.engine.templates import build_sequential_pipeline` is executed
- **THEN** the import SHALL succeed and the function SHALL be callable

#### Scenario: Import broadcast pipeline factory
- **WHEN** `from hecate.engine.templates import build_broadcast_pipeline` is executed
- **THEN** the import SHALL succeed and the function SHALL be callable
