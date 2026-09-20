# Spec Delta

## MODIFIED Requirements

### Requirement: Create skill via API
The system SHALL provide a `POST /api/skills` endpoint that accepts a JSON body with name, description, source, instructions, and optional fields, creates a `SkillModel` record, and returns the created skill. The `SkillModel` SHALL support the source values `system`, `user`, `project`, and `plugin`, and SHALL carry nullable provenance fields `origin` (string) and `plugin_id` (UUID, set only for `source="plugin"` rows). This endpoint SHALL accept only the user-facing values (`system`, `user`, `project`); `plugin` is reserved for the ingestion pipeline. The endpoint SHALL also accept optional invocation-policy flags `model_invocable` and `user_invocable` (both defaulting to `true`), and SHALL reject the combination `auto_load=true` with `model_invocable=false` as a validation error. The `trust_tier` is not client-settable on this endpoint: user- and project-origin skills are created with `community`, and the response SHALL include `provider`, `trust_tier`, `model_invocable`, `user_invocable`, and `content_hash`. A create request whose `name` and resolved `provider` collide with an existing skill in the same workspace SHALL return 409 Conflict; same-name skills with a different `provider` SHALL be allowed to coexist.

#### Scenario: Create skill with all fields
- **WHEN** `POST /api/skills` is called with `{"name": "code-review", "description": "...", "source": "user", "instructions": "..."}`
- **THEN** a new `SkillModel` SHALL be created with `workspace_id` from the authenticated user's workspace, `trust_tier="community"`, `content_hash` computed over the content fields, and the API SHALL return 201 with the full skill data including the registry metadata fields

#### Scenario: Duplicate name in same workspace and same provider
- **WHEN** `POST /api/skills` is called with a name and provider matching an existing skill in the same workspace
- **THEN** the API SHALL return 409 Conflict

#### Scenario: Same name with different provider accepted
- **WHEN** `POST /api/skills` is called with `source="user"` and a name that already exists in the workspace only as a `project` skill
- **THEN** the API SHALL return 201 and the two skills SHALL coexist

#### Scenario: Invalid source value
- **WHEN** `POST /api/skills` is called with `source="invalid"`
- **THEN** the API SHALL return 422 Validation Error

#### Scenario: Plugin source rejected on manual create
- **WHEN** `POST /api/skills` is called with `source="plugin"`
- **THEN** the API SHALL return 422 Validation Error indicating `plugin` is reserved for package ingestion

#### Scenario: Auto-load with hidden model invocation rejected
- **WHEN** `POST /api/skills` is called with `{"auto_load": true, "model_invocable": false}`
- **THEN** the API SHALL return 422 Validation Error explaining the conflict

#### Scenario: Invocation policy flags default to true
- **WHEN** `POST /api/skills` is called without `model_invocable` or `user_invocable`
- **THEN** the created skill SHALL have both flags set to `true`

### Requirement: Import skill from SKILL.md file
The system SHALL provide a `POST /api/skills/import` endpoint that accepts a SKILL.md file (YAML frontmatter + Markdown body), parses it, and creates a `SkillModel` record with `source="user"`, a `content_hash` computed over the same content field set used by agent-version reference manifests (name, instructions, allowed tools, scripts, references), and default invocation-policy flags. A same-name skill of a different `provider` in the workspace SHALL NOT block the import; a same-name `user`-provider skill SHALL be rejected with 409 Conflict.

#### Scenario: Import valid SKILL.md
- **WHEN** `POST /api/skills/import` is called with a file containing valid YAML frontmatter (name, description) and Markdown body
- **THEN** the system SHALL parse the frontmatter into model fields, use the Markdown body as `instructions`, create a `SkillModel` with `source="user"`, `trust_tier="community"`, and a populated `content_hash`, and return 201 with the created skill

#### Scenario: Import SKILL.md missing required frontmatter
- **WHEN** `POST /api/skills/import` is called with a file missing the `name` field in frontmatter
- **THEN** the API SHALL return 422 with error indicating the missing required field

#### Scenario: Import SKILL.md with no frontmatter
- **WHEN** `POST /api/skills/import` is called with a plain Markdown file (no `---` delimiters)
- **THEN** the API SHALL return 422 with error indicating invalid SKILL.md format

#### Scenario: Import same name as project skill allowed
- **WHEN** the workspace already contains a `project` skill with the same name as the imported file
- **THEN** the import SHALL succeed and the imported `user` skill SHALL coexist with the `project` skill

#### Scenario: Import same name and provider rejected
- **WHEN** the workspace already contains a `user`-provider skill with the same name as the imported file
- **THEN** the API SHALL return 409 Conflict

## ADDED Requirements

### Requirement: Skill API exposes provider registry metadata
The skill list and detail endpoints SHALL include, for every skill, its `provider`, `trust_tier`, `model_invocable`, `user_invocable`, and `content_hash`. The `trust_tier` and `provider` SHALL be read-only on workspace-scoped update paths: an update request attempting to change either SHALL be rejected with a validation error. Workspace-scoped update requests MAY change the invocation-policy flags, subject to the auto-load consistency rule.

#### Scenario: List includes registry metadata
- **WHEN** `GET /api/skills` is called in a workspace
- **THEN** each returned skill SHALL include `provider`, `trust_tier`, `model_invocable`, `user_invocable`, and `content_hash`

#### Scenario: Update invocation policy
- **WHEN** `PUT /api/skills/{id}` is called with `{"model_invocable": false}` on a skill with `auto_load=false`
- **THEN** the skill's `model_invocable` SHALL become `false` and the API SHALL return 200 with the updated skill

#### Scenario: Trust tier not updatable via workspace API
- **WHEN** `PUT /api/skills/{id}` is called with `{"trust_tier": "official"}`
- **THEN** the API SHALL return 422 Validation Error without modifying the skill
