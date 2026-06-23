## 1. Model Changes & Migration

- [x] 1.1 Add `commit_message: Mapped[str | None]` field to `PromptVersionModel` in `models/prompt.py`
- [x] 1.2 Update `PromptVersionReadSchema` to include `commit_message: str | None`
- [x] 1.3 Update `PromptUpdateSchema` to accept `commit_message: str | None = None`
- [x] 1.4 Update `PromptCreateSchema` to accept `commit_message: str | None = None`
- [x] 1.5 Create Alembic migration adding `commit_message` TEXT column to `prompt_versions` table, chaining from current head (h9c0d1e2f3a4)

## 2. Config & Protected Labels

- [x] 2.1 Add `PROTECTED_PROMPT_LABELS: list[str]` setting to `core/config.py` with default `["production"]`

## 3. Prompt Service Updates

- [x] 3.1 Update `PromptService.update_prompt()` to accept and persist `commit_message` on new version creation
- [x] 3.2 Update `PromptService.create_prompt()` to accept and persist `commit_message` on initial version
- [x] 3.3 Add `_check_protected_labels(old_labels, new_labels, auth_context)` method that raises `PermissionError` if protected labels are modified by non-admin user
- [x] 3.4 Call `_check_protected_labels` in `update_prompt()` when labels are being changed
- [x] 3.5 Update `PromptService.rollback_to_version()` to preserve commit_message from the target version

## 4. Prompt-to-Trace Linkage

- [x] 4.1 Update `LLMWorker.execute()` to write `metadata.prompt_id` and `metadata.prompt_version` into TraceModel records when agent config references a prompt
- [x] 4.2 Update `LLMWorker.execute_stream()` similarly for streaming LLM calls

## 5. Prompt Analytics Service

- [x] 5.1 Create `services/prompt_analytics_service.py` with `PromptAnalyticsService` class
- [x] 5.2 Implement `compute_diff(prompt_id, from_version, to_version)` — fetch both PromptVersionModel records, compute difflib line-level diff, return structured diff entries with added/removed counts and token delta
- [x] 5.3 Implement `get_version_analytics(prompt_id, version, days=7)` — query TraceModel filtering by metadata prompt_id and prompt_version, aggregate call count, avg latency, total tokens, error rate, and cost
- [x] 5.4 Implement `compare_versions(prompt_id, from_version, to_version)` — fetch analytics for both versions and return side-by-side metrics with deltas
- [x] 5.5 Implement `generate_change_summary(prompt_id, version)` — compute diff against predecessor version, send to LLMService for human-readable summary generation

## 6. API Endpoints

- [x] 6.1 Add `GET /api/prompts/{id}/diff?from_version=X&to_version=Y` endpoint returning structured diff
- [x] 6.2 Add `GET /api/prompts/{id}/analytics?version=X&days=7` endpoint returning per-version metrics
- [x] 6.3 Add `GET /api/prompts/{id}/compare?from_version=X&to_version=Y` endpoint returning side-by-side metrics
- [x] 6.4 Add `POST /api/prompts/{id}/versions/{version}/summary` endpoint returning AI-generated change summary
- [x] 6.5 Update existing `PUT /api/prompts/{id}` endpoint to enforce protected label RBAC — return 403 Forbidden when non-admin user modifies protected labels
- [x] 6.6 Update existing `GET /api/prompts/{id}/versions` to include `commit_message` in version responses

## 7. Tests

- [x] 7.1 Test `compute_diff` — identical templates return zero added/removed, different templates return correct diff entries and counts
- [x] 7.2 Test `get_version_analytics` — traces with matching metadata produce correct aggregated metrics, empty result for no traces
- [x] 7.3 Test `compare_versions` — side-by-side metrics with correct delta calculations
- [x] 7.4 Test `generate_change_summary` — returns non-empty summary string for changed versions, handles first version gracefully
- [x] 7.5 Test commit message persistence — create prompt with commit_message, update with commit_message, verify version listing includes commit_messages
- [x] 7.6 Test protected labels RBAC — admin can add "production" label, non-admin gets 403 Forbidden, non-protected labels work for all roles
- [x] 7.7 Test diff API endpoint — 404 for non-existent versions, correct diff response structure
- [x] 7.8 Test analytics API endpoint — correct response structure with daily_breakdown

## 8. Verification

- [x] 8.1 Run `ruff check src/hecate/ tests/` — zero errors
- [x] 8.2 Run `ruff format --check src/ tests/` — zero changes needed
- [x] 8.3 Run `mypy src/` — zero errors
- [x] 8.4 Run `python -m pytest tests/ -q` — all tests pass
