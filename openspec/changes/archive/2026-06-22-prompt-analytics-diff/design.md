## Context

The platform has Prompt CRUD & Versioning (8.5a): `PromptModel` (name, current_version), `PromptVersionModel` (prompt_id, version, template, variables, labels), `PromptService` (create, update, rollback, get_by_label), and 9 API endpoints. `TraceModel` has `metadata_` JSON field but no explicit prompt reference. `LLMWorker` executes prompts but does not record which prompt version was used.

Research covered 10+ platforms: LangSmith (explicit FK prompt-to-trace, variant analytics dashboard), LangFuse (metadata linkage, per-version time-series), Vellum (release review workflow, approval gates), Dify (commit messages, workspace role-based publishing), AgentArts (APM integration, version comparison), Humanloop (AI-generated change summaries), Promptflow (variant experiments).

## Goals / Non-Goals

**Goals:**
- Version diff with line-level changes, variable changes, and token count comparison
- Per-version analytics with trace-derived metrics via metadata linkage
- Commit messages on version creation
- AI-assisted change summaries using LLMService
- Protected labels with RBAC enforcement
- Side-by-side version comparison with metrics

**Non-Goals:**
- A/B testing for prompt versions (reuse existing 6.8a A/B Testing infrastructure — future integration)
- Prompt regression gate (auto-trigger evaluation suite on new version — future integration)
- Visual prompt editor UI (this is backend + API only)
- Prompt marketplace / sharing (separate feature)
- Multi-language prompt translation (separate feature)

## Decisions

### D1: Trace metadata linkage for analytics (no migration on TraceModel)

**Decision**: Use `TraceModel.metadata_` JSON field to store prompt references. When LLMWorker uses a prompt, it writes `metadata.prompt_id` and `metadata.prompt_version` into the trace record. Analytics queries filter traces by these metadata fields.

**Rationale**: LangFuse uses this pattern successfully. It avoids a migration on TraceModel (which is high-traffic and has many rows). JSON metadata filtering is supported by all database backends. The alternative of adding a `prompt_version_id` FK column would require a migration on the largest table and add index overhead.

**Alternatives considered**:
- Add `prompt_version_id` FK to TraceModel (LangSmith pattern) — precise JOINs but heavy migration on large table
- Time-window inference (Dify pattern) — imprecise when multiple versions coexist
- Separate prompt_usage table — over-engineered for v1

### D2: difflib-based version diff with structured output

**Decision**: Use Python's `difflib` (standard library) to compute line-level diffs between version templates. Return a structured JSON array of diff entries (type: context/added/removed, content, line_number) rather than raw unified diff text.

**Rationale**: All surveyed platforms use line-level diff as the base layer. difflib is battle-tested, zero-dependency, and produces accurate results. The structured JSON format enables frontend rendering in any diff viewer (side-by-side, inline, or unified). Raw unified diff is also available as an alternative format via query parameter.

**Diff output schema**:
```json
{
  "from_version": 2,
  "to_version": 3,
  "from_commit_message": "Initial version",
  "to_commit_message": "Add citation instructions",
  "added_lines": 3,
  "removed_lines": 1,
  "token_delta": 25,
  "diff_entries": [
    {"type": "context", "from_line": 1, "to_line": 1, "content": "You are a helpful assistant."},
    {"type": "removed", "from_line": 2, "to_line": null, "content": "Be concise."},
    {"type": "added", "from_line": null, "to_line": 2, "content": "Always cite your sources."},
    {"type": "added", "from_line": null, "to_line": 3, "content": "Provide detailed explanations."}
  ]
}
```

### D3: Protected labels via config + AuthContext role check

**Decision**: Define protected labels in `core/config.py` as `PROTECTED_PROMPT_LABELS` (default: `["production"]`). When `PromptService.update_prompt` is called with label changes that add or remove a protected label, the service checks `AuthContext.role`. Only `admin` role users can modify protected labels. Non-admin users receive 403 Forbidden.

**Rationale**: Dify uses workspace role-based publishing (editor → preview, admin → publish). Vellum has a full release review workflow. For v1, a simpler config-based approach with role check is sufficient. The config list makes it extensible — organizations can add custom protected labels (e.g., "staging-eu", "compliance-approved") without code changes.

**Alternatives considered**:
- Full approval workflow (Vellum pattern) — too heavy for v1, requires reviewer model, notification dispatch, approval state machine
- Per-label RBAC rules in database — flexible but adds complexity, config is sufficient for common cases
- Environment isolation (Dify pattern) — would require separate prompt stores per environment, over-engineered

### D4: Commit message on PromptVersionModel

**Decision**: Add `commit_message: str | None` field to `PromptVersionModel`. `PromptUpdateSchema` accepts optional `commit_message`. `PromptService.update_prompt` persists it on the new version. `PromptVersionReadSchema` includes it in responses.

**Rationale**: Dify, LangSmith, LangFuse all support manual commit messages. Vellum requires them for review. For v1, manual input is sufficient. The AI-assisted summary feature (D5) complements this by generating summaries on demand.

### D5: AI-assisted change summary via LLMService

**Decision**: Add `POST /api/prompts/{id}/versions/{version}/summary` endpoint that generates a human-readable change description by sending the version diff to LLMService. The LLM prompt instructs it to summarize what changed and why it might matter (e.g., "Changed tone from formal to casual, added 2 new instructions about citation").

**Rationale**: Humanloop auto-generates change summaries. Vellum provides AI impact analysis during review. This is a differentiator — developers get instant understanding of changes without reading diffs manually. The summary is generated on-demand (not at creation time) to avoid LLM cost overhead on every version update.

### D6: Analytics aggregation via SQL queries on metadata JSON

**Decision**: `PromptAnalyticsService` queries TraceModel filtering by `metadata_->>'prompt_id' = X AND metadata_->>'prompt_version' = N`, then aggregates: COUNT(*) for calls, AVG(end_time - start_time) for latency, SUM(usage->>'total_tokens') for tokens, COUNT(status='error')/COUNT(*) for error rate. Cost is computed via CostService for the filtered trace set.

**Rationale**: LangFuse uses the same metadata-based aggregation pattern. PostgreSQL supports `->>` JSON path operator with GIN index for efficient filtering. SQLite (test) supports `json_extract()`. The abstraction layer in SQLAlchemy handles both.

## Risks / Trade-offs

- **[Risk] Metadata JSON queries may be slow on large trace tables** → Mitigate by adding a GIN index on `traces.metadata` in a future migration if performance issues arise. For v1, the query scans by workspace_id + time range first, then filters by metadata.

- **[Risk] LLMWorker doesn't always know which prompt version it's using** → Mitigate by making the metadata write conditional — only when `agent_config.prompt_id` is set. If no prompt is configured, no metadata is written and that trace won't appear in analytics.

- **[Trade-off] No approval workflow in v1** → Protected labels with role check is sufficient for most teams. Full review workflow can be added later if needed.

- **[Trade-off] AI summary is on-demand, not automatic** → Avoids LLM cost on every version update. Users trigger it when they need a summary. This matches Humanloop's pattern.

- **[Trade-off] No A/B testing integration in v1** → The existing 6.8a A/B Testing infrastructure can be leveraged later. For now, version comparison analytics provides the data to make manual deployment decisions.
