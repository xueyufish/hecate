## ADDED Requirements

### Requirement: Prompt version diff API
The system SHALL expose `GET /api/prompts/{id}/diff?from_version=X&to_version=Y` that computes a line-level diff between two prompt versions using `difflib`, returning structured diff entries with added/removed/context lines, line numbers, commit messages, and token count delta.

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
The system SHALL expose `GET /api/prompts/{id}/analytics?version=X&days=7` that aggregates trace-derived metrics for a specific prompt version by querying TraceModel records where `metadata_->>'prompt_id'` and `metadata_->>'prompt_version'` match.

#### Scenario: Analytics for active version
- **WHEN** `GET /api/prompts/{id}/analytics?version=3&days=7` is called and traces exist with matching metadata
- **THEN** the response SHALL include `total_calls`, `avg_latency_ms`, `total_tokens`, `error_rate`, `total_cost`, and `daily_breakdown` array

#### Scenario: Analytics for version with no traces
- **WHEN** analytics is requested for a version that has no trace data
- **THEN** the response SHALL return zero values for all metrics (`total_calls=0`, `avg_latency_ms=0`, etc.)

### Requirement: Version comparison API
The system SHALL expose `GET /api/prompts/{id}/compare?from_version=X&to_version=Y` that returns side-by-side metrics for two prompt versions, enabling data-driven deployment decisions.

#### Scenario: Compare two versions
- **WHEN** `GET /api/prompts/{id}/compare?from_version=2&to_version=3` is called
- **THEN** the response SHALL include per-version metrics (calls, avg latency, tokens, error rate, cost) and delta values showing the difference

### Requirement: AI-assisted change summary API
The system SHALL expose `POST /api/prompts/{id}/versions/{version}/summary` that generates a human-readable change description by sending the version diff to LLMService for summarization.

#### Scenario: Generate summary for version with changes
- **WHEN** the summary endpoint is called for a version that differs from its predecessor
- **THEN** the response SHALL include a natural language summary describing what changed (e.g., "Added instructions about citing sources and changed tone to be more formal")

#### Scenario: Generate summary for first version
- **WHEN** the summary endpoint is called for version 1 (no predecessor)
- **THEN** the response SHALL indicate this is the initial version with no changes to summarize

### Requirement: Prompt analytics service
The system SHALL provide a `PromptAnalyticsService` in `services/prompt_analytics_service.py` that computes version diffs, aggregates trace metrics per prompt version, compares two versions' metrics, and generates AI change summaries via LLMService.

#### Scenario: Compute diff between versions
- **WHEN** `compute_diff(prompt_id, from_version, to_version)` is called
- **THEN** the service SHALL fetch both PromptVersionModel records, compute difflib diff, count additions/removals, estimate token delta, and return a structured diff result

#### Scenario: Aggregate metrics for a version
- **WHEN** `get_version_analytics(prompt_id, version, days)` is called
- **THEN** the service SHALL query TraceModel filtering by metadata prompt_id and prompt_version, aggregate call count, latency, tokens, error rate, and cost via CostService
