## Purpose

Defines prompt version management: commit messages on versions, protected label enforcement, version trace linkage, and the workspace/role access boundary over the prompt management API.

## Requirements

### Requirement: Workspace and role access boundary
Every prompt management entry (create, list, detail, update, delete, version list/detail, rollback, by-label lookup, diff, analytics, compare, AI summary) SHALL require an explicit active workspace — 403 ``NO_WORKSPACE`` before any query, system scope included; an explicit zero-UUID workspace SHALL remain a legal scope. Reads SHALL require at least VIEWER; create, update, delete, rollback, and AI summary SHALL require at least EDITOR. Roles SHALL be taken from the server-resolved auth context only. Create, list, and by-label SHALL check the role before any business query; by-id entries SHALL first probe visibility with a minimal metadata query, then apply the role floor, and only then read templates, compute diffs/statistics, invoke the summary model, or write. Missing, foreign-workspace, soft-deleted, and workspace-inconsistent resources SHALL share one constant 404 that leaks neither the reason nor any evidence.

#### Scenario: Workspace-less identity
- **WHEN** an identity without an active workspace (member or system scope) calls any prompt management entry
- **THEN** the API SHALL return 403 ``NO_WORKSPACE`` and no database query SHALL run

#### Scenario: Viewer attempts a write or summary
- **WHEN** a VIEWER calls create, update, delete, rollback, or the AI summary entry
- **THEN** the API SHALL return 403 and no version, mutation, or model call SHALL occur

#### Scenario: Invisible resource
- **WHEN** a by-id entry targets a prompt that is missing, foreign to the caller's workspace, soft-deleted, or whose needed version is workspace-inconsistent
- **THEN** the API SHALL return the constant 404 for that entry class, identical across all failure causes

### Requirement: Prompt and version workspace consistency
The service layer SHALL bind every read and write to the caller's explicit workspace — never relying on the API precheck alone. A version SHALL participate in reads, copies, rollbacks, and analytics only when it belongs to the target prompt, shares the parent prompt's workspace, and is not soft-deleted. New versions created by create, update, and rollback SHALL explicitly inherit the parent prompt's workspace rather than the model default. Legacy versions whose workspace disagrees with the parent prompt SHALL be rejected (never listed, read, copied, rolled back, or analyzed) and SHALL NOT be auto-corrected or cross-workspace backfilled; historical backfill is a separate concern.

Reads that need the current version, including prompt detail and update, SHALL include that version's binding in their metadata visibility probe before the role decision. Authorized list and label queries SHALL refresh cached ORM state and enforce parent workspace consistency in the content query itself; a previous parent lookup SHALL NOT authorize newly foreign version content. Label lookup SHALL use exact JSON array membership, including when a version carries multiple labels, rather than serialized-array substring matching.

#### Scenario: New version inherits the prompt workspace
- **WHEN** a prompt in a non-zero workspace gets a new version via create, update, or rollback
- **THEN** the new version row SHALL carry the parent prompt's workspace id

#### Scenario: Legacy inconsistent version
- **WHEN** a version row's workspace differs from its parent prompt's workspace
- **THEN** version listings SHALL exclude it and version-scoped entries (detail, rollback, diff, analytics, compare, summary) SHALL return the constant 404

#### Scenario: Label lookup stays workspace-scoped
- **WHEN** a by-label lookup runs and a newer foreign-workspace version carries the same label
- **THEN** parent/child workspace consistency SHALL be enforced before ordering and LIMIT, the foreign version SHALL NOT shadow the caller's own, and versions of soft-deleted prompts SHALL NOT be reachable by label

### Requirement: Write-path binding recheck
Write paths (update, delete, rollback) SHALL re-read the prompt — and rollback the target version — inside the caller's transaction with fresh reads (identity-map refresh, row lock where the dialect supports it) before mutating. A prompt moved to another workspace, deleted, or rebound after the API precheck SHALL be refused with the constant 404 and nothing SHALL be written; refusals SHALL NOT change names, soft-delete markers, ``current_version``, versions, or labels.

The actual write SHALL condition on the caller's workspace, live parent and source-version binding, and the expected current-version pointer. A locking read alone SHALL NOT be treated as sufficient protection on SQLite. Version allocation SHALL claim the next pointer before inserting content; a competing pointer advance SHALL return 409 without a duplicate version or partial write. Deletion SHALL similarly condition its UPDATE on the caller's workspace. Rejected methods SHALL leave no forbidden changes even if a caller subsequently commits the transaction.

#### Scenario: Prompt moved after the precheck
- **WHEN** a prompt is moved to another workspace between the API visibility probe and the service write
- **THEN** the write SHALL return 404, no new version or soft delete SHALL be persisted, and the prompt SHALL NOT be followed into the other workspace

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
The system SHALL enforce role-based access control on protected prompt labels. Labels listed in `PROTECTED_PROMPT_LABELS` config (default: `["production"]`) SHALL require `admin` role to add, remove, or carry onto any new version — covering explicit create/update payloads, label carry-over by omission, and rollback copies. Non-admin users attempting any of these SHALL receive 403 Forbidden. Unprotected label operations SHALL remain EDITOR-capable, and versions legally published without labels (e.g. by prompt self-optimization) SHALL remain fully manageable and readable.

Protected-label checks SHALL also run when an update supplies labels but omits the template; omission SHALL NOT silently accept a forbidden label request.

#### Scenario: Admin adds protected label
- **WHEN** a user with `admin` role updates a prompt adding the "production" label
- **THEN** the label SHALL be persisted on the new version

#### Scenario: Non-admin blocked from protected label
- **WHEN** a user with `editor` role attempts to add the "production" label
- **THEN** the API SHALL return 403 Forbidden with an error message indicating the label is protected

#### Scenario: Non-admin blocked from carrying a protected label by omission
- **WHEN** a user with `editor` role updates a prompt whose current version carries "production" without providing labels
- **THEN** the API SHALL return 403 Forbidden and no new version SHALL be created

#### Scenario: Non-admin blocked from rolling back onto a protected label
- **WHEN** a user with `editor` role rolls back to a version carrying "production"
- **THEN** the API SHALL return 403 Forbidden and no copy version SHALL be created

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
