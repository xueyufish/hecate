## MODIFIED Requirements

### Requirement: Create skill via API
The system SHALL provide a `POST /api/skills` endpoint that accepts a JSON body with name, description, source, instructions, and optional fields, creates a `SkillModel` record, and returns the created skill. The `SkillModel` SHALL support the source values `system`, `user`, `project`, and `plugin`, and SHALL carry nullable provenance fields `origin` (string) and `plugin_id` (UUID, set only for `source="plugin"` rows). This endpoint SHALL accept only the user-facing values (`system`, `user`, `project`); `plugin` is reserved for the ingestion pipeline. Responses SHALL include the `origin` and `plugin_id` fields (null for non-plugin skills).

#### Scenario: Create skill with all fields
- **WHEN** `POST /api/skills` is called with `{"name": "code-review", "description": "...", "source": "user", "instructions": "..."}`
- **THEN** a new `SkillModel` SHALL be created with `workspace_id` from the authenticated user's workspace, and the API SHALL return 201 with the full skill data

#### Scenario: Duplicate name in same workspace
- **WHEN** `POST /api/skills` is called with a name that already exists in the same workspace
- **THEN** the API SHALL return 409 Conflict

#### Scenario: Invalid source value
- **WHEN** `POST /api/skills` is called with `source="invalid"`
- **THEN** the API SHALL return 422 Validation Error

#### Scenario: Plugin source rejected on manual create
- **WHEN** `POST /api/skills` is called with `source="plugin"`
- **THEN** the API SHALL return 422 Validation Error indicating `plugin` is reserved for package ingestion

## ADDED Requirements

### Requirement: Plugin-derived skills are lifecycle-managed
Skills with `source="plugin"` SHALL be readable through the skill list and detail endpoints with their provenance fields (`origin`, `plugin_id`) visible. Update and delete operations on a plugin-derived skill via the skill API SHALL be rejected with 409 Conflict directing the caller to the owning plugin's lifecycle (enable/disable/uninstall); these rows are managed exclusively by the ingestion pipeline.

#### Scenario: List includes plugin-derived skills
- **WHEN** `GET /api/skills` is called in a workspace with an installed agent-plugin package
- **THEN** the imported skills appear with `source="plugin"` and their `origin` and `plugin_id` populated

#### Scenario: Update plugin-derived skill rejected
- **WHEN** `PUT /api/skills/{id}` is called for a skill with `source="plugin"`
- **THEN** the API SHALL return 409 Conflict without modifying the skill

#### Scenario: Delete plugin-derived skill rejected
- **WHEN** `DELETE /api/skills/{id}` is called for a skill with `source="plugin"`
- **THEN** the API SHALL return 409 Conflict without deleting the skill
