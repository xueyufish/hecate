## Why

The platform has Prompt CRUD & Versioning (8.5a) with immutable version snapshots, labels, and rollback. However, there is no way to compare versions side-by-side, measure performance per version, protect production labels from unauthorized changes, or attach change summaries. This leaves prompt management blind — teams cannot answer "what changed between v2 and v3?" or "is v3 performing better than v2?" and production labels can be modified by any user.

## What Changes

- Add version diff API: `GET /api/prompts/{id}/diff?from_version=X&to_version=Y` returning line-level diff, variable changes, and token count comparison
- Add per-version analytics API: `GET /api/prompts/{id}/analytics?version=X&days=7` returning trace-derived metrics (call count, avg latency, total tokens, error rate, total cost) aggregated from trace metadata linkage
- Add prompt-to-trace linkage: LLMWorker writes `metadata.prompt_id` and `metadata.prompt_version` into TraceModel records when a prompt template is used during agent execution
- Add commit message support: `PromptVersionModel` gains `commit_message: str | None` field; `PromptUpdateSchema` accepts optional `commit_message`; version creation persists it
- Add AI-assisted change summary: `POST /api/prompts/{id}/versions/{version}/summary` generates a human-readable change description using LLMService to compare the version diff against the previous version
- Add protected labels: config-defined `PROTECTED_PROMPT_LABELS = ["production"]` list; modifying labels that include protected entries requires `admin` role via AuthContext check; non-admin users attempting to add/remove protected labels receive 403 Forbidden
- Add version comparison analytics: `GET /api/prompts/{id}/compare?from_version=X&to_version=Y` returning side-by-side metrics for two versions (calls, latency, tokens, cost, error rate) enabling data-driven deployment decisions

## Capabilities

### New Capabilities
- `prompt-analytics`: Per-version performance analytics with trace-derived metrics (latency, tokens, cost, error rate), version comparison, and AI-assisted change summaries

### Modified Capabilities
- `prompt-version-management`: PromptVersionModel gains commit_message field; PromptUpdateSchema accepts commit_message; update_prompt persists commit_message on new version creation; protected label enforcement via role check; LLMWorker writes prompt_id and prompt_version into trace metadata during execution

## Impact

- **New files**: `services/prompt_analytics_service.py` (analytics queries + diff computation + AI summary)
- **Modified models**: `models/prompt.py` (add commit_message to PromptVersionModel + schemas)
- **Modified services**: `services/prompt_service.py` (commit_message on update, protected label checks), `engine/workers/llm_worker.py` (write prompt metadata to trace)
- **Modified API**: `api/management/prompts.py` (diff, analytics, compare, summary endpoints + protected label enforcement)
- **Migration**: Add `commit_message` TEXT column to `prompt_versions` table
- **Config**: Add `PROTECTED_PROMPT_LABELS` setting to `core/config.py`
- **Tests**: Diff computation tests, analytics query tests, protected label RBAC tests, commit message tests
