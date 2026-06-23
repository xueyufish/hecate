## ADDED Requirements

### Requirement: Commit message on prompt version
The system SHALL support a `commit_message: str | None` field on `PromptVersionModel`. When provided during prompt update, the commit message SHALL be persisted on the newly created version record.

#### Scenario: Update prompt with commit message
- **WHEN** `PUT /api/prompts/{id}` is called with `{"template": "...", "commit_message": "Add citation instructions"}`
- **THEN** the new version SHALL be created with `commit_message` persisted and returned in the version response

#### Scenario: Update prompt without commit message
- **WHEN** `PUT /api/prompts/{id}` is called without a `commit_message` field
- **THEN** the new version SHALL be created with `commit_message=None`

#### Scenario: Version listing includes commit messages
- **WHEN** `GET /api/prompts/{id}/versions` is called
- **THEN** each version in the response SHALL include its `commit_message` field (may be null)

### Requirement: Protected label enforcement
The system SHALL enforce role-based access control on protected prompt labels. Labels listed in `PROTECTED_PROMPT_LABELS` config (default: `["production"]`) SHALL require `admin` role to add or remove. Non-admin users attempting to modify protected labels SHALL receive 403 Forbidden.

#### Scenario: Admin adds protected label
- **WHEN** a user with `admin` role updates a prompt adding the "production" label
- **THEN** the label SHALL be persisted on the new version

#### Scenario: Non-admin blocked from protected label
- **WHEN** a user with `editor` role attempts to add the "production" label
- **THEN** the API SHALL return 403 Forbidden with an error message indicating the label is protected

#### Scenario: Non-admin can modify non-protected labels
- **WHEN** a user with `editor` role updates a prompt adding the "development" label
- **THEN** the update SHALL succeed since "development" is not in PROTECTED_PROMPT_LABELS

### Requirement: Prompt version trace linkage
The system SHALL write prompt identification into TraceModel metadata when LLMWorker executes using a configured prompt. The metadata SHALL include `prompt_id` (UUID string) and `prompt_version` (integer) when the agent configuration references a prompt.

#### Scenario: LLM call with prompt writes trace metadata
- **WHEN** LLMWorker executes an LLM call for an agent with `prompt_id` configured
- **THEN** the resulting TraceModel record SHALL have `metadata_.prompt_id` and `metadata_.prompt_version` populated

#### Scenario: LLM call without prompt skips metadata
- **WHEN** LLMWorker executes an LLM call for an agent without `prompt_id` configured
- **THEN** no prompt metadata SHALL be written to the trace record
