## Purpose

Defines prompt analytics: version diffing, per-version analytics, version comparison, AI-assisted change summaries, the prompt analytics service, and the workspace/trace authorization boundary over the analytics API.

## Requirements

### Requirement: Workspace boundary on analytics entries
Diff, analytics, compare, and summary entries SHALL require an explicit active workspace (403 ``NO_WORKSPACE`` before any query, system scope included) and at least VIEWER for reads; the AI summary SHALL require at least EDITOR because it invokes a model. By-id entries SHALL validate the prompt and every needed version (the summary of version N>1 also needs N-1) with a minimal visibility probe before the role floor, and only then read templates, compute statistics, or call the model. Missing, foreign, soft-deleted, and workspace-inconsistent versions SHALL share one constant 404 — a nonexistent version SHALL NOT produce a "zero calls" success response.

#### Scenario: Nonexistent version in analytics
- **WHEN** analytics or compare is requested for a version that is missing or workspace-inconsistent
- **THEN** the API SHALL return the constant 404, not a zero-valued success

#### Scenario: Compare validates both versions first
- **WHEN** compare is requested with a foreign or invisible second version
- **THEN** the API SHALL return 404 before any statistics run

#### Scenario: Summary floor
- **WHEN** a VIEWER requests an AI change summary
- **THEN** the API SHALL return 403 and no model call or failure-fallback SHALL run

### Requirement: Trace authorization via Session/Agent association
TraceModel carries no workspace column; the `prompt_id` / `prompt_version` metadata SHALL act only as a metric selector, never as a tenant authorization. A trace SHALL participate in aggregates only when its Session row (or, for session-less traces, its Agent row) is live and resolves into the caller's workspace: traces with a session and an agent whose associations disagree on the workspace, orphaned traces (neither association), foreign-workspace associations, soft-deleted Session/Agent rows, and soft-deleted traces SHALL be excluded. The summary aggregates and the daily breakdown SHALL use the same scoping, and the version marker SHALL match both numeric and string storage shapes. Authorized paths SHALL return real statistics — never green-light by filtering everything out.

Session attribution SHALL also require the session's linked Agent to be live in the same workspace; a local Session row pointing to a foreign or deleted Agent SHALL NOT authorize a trace. Version selectors SHALL accept integer and canonical string markers, ignore malformed/non-integer markers including booleans, and avoid integer casts that can fail on arbitrary trace metadata. The time window SHALL be applied in the database before materializing rows; totals and daily breakdown SHALL consume the same filtered population.

#### Scenario: Workspace-visible traces count
- **WHEN** analytics runs for a version with traces whose session (or session-less agent) resolves into the caller's workspace and metadata selects that version
- **THEN** the response SHALL include real totals, latency, tokens, error rate, and a daily breakdown whose calls sum to the total

#### Scenario: Foreign metadata never authorizes
- **WHEN** a trace in another workspace carries metadata selecting the caller's prompt version
- **THEN** the trace SHALL NOT participate in the caller's aggregates

#### Scenario: Contradictory association excluded
- **WHEN** a trace's session resolves into workspace A while its agent resolves into workspace B
- **THEN** the trace SHALL NOT participate in either workspace's aggregates

### Requirement: Prompt version diff API
The system SHALL expose `GET /api/prompts/{id}/diff?from_version=X&to_version=Y` that computes a line-level diff between two prompt versions using `difflib`, returning structured diff entries with added/removed/context lines, line numbers, commit messages, and token count delta. Both versions SHALL be workspace-validated before any content is read.

#### Scenario: Diff between two versions
- **WHEN** `GET /api/prompts/{id}/diff?from_version=2&to_version=3` is called
- **THEN** the response SHALL include `diff_entries` array with `{type, from_line, to_line, content}`, `added_lines` count, `removed_lines` count, `token_delta`, and both versions' commit messages

#### Scenario: Diff with identical versions
- **WHEN** a diff is requested between two versions with identical templates
- **THEN** the response SHALL have `added_lines=0`, `removed_lines=0`, and all entries as `type="context"`

#### Scenario: Diff with non-existent version
- **WHEN** a diff is requested with a version number that doesn't exist
- **THEN** the API SHALL return 404 Not Found

### Requirement: Per-version analytics API
The system SHALL expose `GET /api/prompts/{id}/analytics?version=X&days=7` that aggregates trace-derived metrics for a specific prompt version by querying TraceModel records where `metadata_->>'prompt_id'` and `prompt_version` match, restricted to traces authorized through Session/Agent association into the caller's workspace.

#### Scenario: Analytics for active version
- **WHEN** `GET /api/prompts/{id}/analytics?version=3&days=7` is called and workspace-visible traces exist with matching metadata
- **THEN** the response SHALL include `total_calls`, `avg_latency_ms`, `total_tokens`, `error_rate`, `total_cost`, and `daily_breakdown` array

#### Scenario: Analytics for version with no traces
- **WHEN** analytics is requested for a visible version that has no authorized trace data
- **THEN** the response SHALL return zero values for all metrics (`total_calls=0`, `avg_latency_ms=0`, etc.)

### Requirement: Version comparison API
The system SHALL expose `GET /api/prompts/{id}/compare?from_version=X&to_version=Y` that returns side-by-side metrics for two prompt versions, enabling data-driven deployment decisions. Both versions SHALL be validated before any aggregation.

#### Scenario: Compare two versions
- **WHEN** `GET /api/prompts/{id}/compare?from_version=2&to_version=3` is called
- **THEN** the response SHALL include per-version metrics (calls, avg latency, tokens, error rate, cost) and delta values showing the difference

### Requirement: AI-assisted change summary API
The system SHALL expose `POST /api/prompts/{id}/versions/{version}/summary` that generates a human-readable change description by sending the version diff to LLMService for summarization. Workspace binding and role SHALL be verified before every shortcut: the initial-version response and the no-change response SHALL only be reachable for a workspace-visible version, and denied requests SHALL never invoke the model or enter its failure-fallback branch.

#### Scenario: Generate summary for version with changes
- **WHEN** the summary endpoint is called for a version that differs from its predecessor
- **THEN** the response SHALL include a natural language summary describing what changed (e.g., "Added instructions about citing sources and changed tone to be more formal")

#### Scenario: Generate summary for first version
- **WHEN** the summary endpoint is called for version 1 (no predecessor)
- **THEN** the response SHALL indicate this is the initial version with no changes to summarize

#### Scenario: Shortcuts stay behind the boundary
- **WHEN** the summary endpoint is called for an invisible version or by a caller below EDITOR
- **THEN** the API SHALL return 404/403 respectively and neither the shortcut responses nor the model call SHALL run

### Requirement: Prompt analytics service
The system SHALL provide a `PromptAnalyticsService` in `services/prompt_analytics_service.py` that computes version diffs, aggregates trace metrics per prompt version, compares two versions' metrics, and generates AI change summaries via LLMService. All service methods SHALL take the caller's workspace explicitly and re-validate version visibility inside their own transaction.

#### Scenario: Compute diff between versions
- **WHEN** `compute_diff(prompt_id, from_version, to_version, workspace_id)` is called
- **THEN** the service SHALL fetch both workspace-consistent PromptVersionModel records, compute difflib diff, count additions/removals, estimate token delta, and return a structured diff result

#### Scenario: Aggregate metrics for a version
- **WHEN** `get_version_analytics(prompt_id, version, workspace_id, days)` is called
- **THEN** the service SHALL validate the version, select traces by metadata prompt_id and prompt_version (numeric or string marker) and Session/Agent workspace association, and aggregate call count, latency, tokens, and error rate
