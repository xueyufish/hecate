## ADDED Requirements

### Requirement: Create skill via API
The system SHALL provide a `POST /api/skills` endpoint that accepts a JSON body with name, description, source, instructions, and optional fields, creates a `SkillModel` record, and returns the created skill.

#### Scenario: Create skill with all fields
- **WHEN** `POST /api/skills` is called with `{"name": "code-review", "description": "...", "source": "user", "instructions": "..."}`
- **THEN** a new `SkillModel` SHALL be created with `workspace_id` from the authenticated user's workspace, and the API SHALL return 201 with the full skill data

#### Scenario: Duplicate name in same workspace
- **WHEN** `POST /api/skills` is called with a name that already exists in the same workspace
- **THEN** the API SHALL return 409 Conflict

#### Scenario: Invalid source value
- **WHEN** `POST /api/skills` is called with `source="invalid"`
- **THEN** the API SHALL return 422 Validation Error

### Requirement: Update skill via API
The system SHALL provide a `PUT /api/skills/{id}` endpoint that accepts a JSON body with optional fields to update, modifies the `SkillModel` record, and returns the updated skill.

#### Scenario: Update skill description
- **WHEN** `PUT /api/skills/{id}` is called with `{"description": "Updated description"}`
- **THEN** the skill's description SHALL be updated and the API SHALL return 200 with full skill data

#### Scenario: Update non-existent skill
- **WHEN** `PUT /api/skills/{id}` is called with a non-existent ID
- **THEN** the API SHALL return 404 Not Found

### Requirement: Delete skill via API
The system SHALL provide a `DELETE /api/skills/{id}` endpoint that soft-deletes the `SkillModel` record (sets `deleted_at` timestamp).

#### Scenario: Delete existing skill
- **WHEN** `DELETE /api/skills/{id}` is called for an existing skill
- **THEN** the skill SHALL be soft-deleted (deleted_at set) and the API SHALL return 200

#### Scenario: Delete non-existent skill
- **WHEN** `DELETE /api/skills/{id}` is called with a non-existent ID
- **THEN** the API SHALL return 404 Not Found

### Requirement: Import skill from SKILL.md file
The system SHALL provide a `POST /api/skills/import` endpoint that accepts a SKILL.md file (YAML frontmatter + Markdown body), parses it, and creates a `SkillModel` record.

#### Scenario: Import valid SKILL.md
- **WHEN** `POST /api/skills/import` is called with a file containing valid YAML frontmatter (name, description) and Markdown body
- **THEN** the system SHALL parse the frontmatter into model fields, use the Markdown body as `instructions`, create a `SkillModel` with `source="user"`, and return 201 with the created skill

#### Scenario: Import SKILL.md missing required frontmatter
- **WHEN** `POST /api/skills/import` is called with a file missing the `name` field in frontmatter
- **THEN** the API SHALL return 422 with error indicating the missing required field

#### Scenario: Import SKILL.md with no frontmatter
- **WHEN** `POST /api/skills/import` is called with a plain Markdown file (no `---` delimiters)
- **THEN** the API SHALL return 422 with error indicating invalid SKILL.md format

### Requirement: Manage agent-skill associations
The system SHALL provide endpoints to add and remove skill associations from an agent.

#### Scenario: Add skill to agent
- **WHEN** `POST /api/agents/{id}/skills` is called with `{"skill_name": "code-review"}`
- **THEN** the skill name SHALL be appended to the agent's `skills` list if not already present, and the API SHALL return 200 with the updated skills list

#### Scenario: Add duplicate skill to agent
- **WHEN** `POST /api/agents/{id}/skills` is called with a skill name already in the agent's `skills` list
- **THEN** the API SHALL return 200 with the unchanged skills list (idempotent)

#### Scenario: Remove skill from agent
- **WHEN** `DELETE /api/agents/{id}/skills/{skill_name}` is called
- **THEN** the skill name SHALL be removed from the agent's `skills` list and the API SHALL return 200

#### Scenario: Remove non-existent skill from agent
- **WHEN** `DELETE /api/agents/{id}/skills/{skill_name}` is called with a skill name not in the agent's `skills` list
- **THEN** the API SHALL return 200 with the unchanged skills list (idempotent)

### Requirement: List skills filtered by workspace
The existing `GET /api/skills` endpoint SHALL filter results by the authenticated user's workspace_id, not return skills from other workspaces.

#### Scenario: List skills returns only workspace skills
- **WHEN** `GET /api/skills` is called by a user in workspace A
- **THEN** only skills with `workspace_id=A` SHALL be returned, excluding skills from other workspaces

#### Scenario: System skills visible to all workspaces
- **WHEN** `GET /api/skills` is called by any user
- **THEN** skills with `workspace_id=00000000` (system skills) SHALL also be included in results
