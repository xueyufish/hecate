## 1. Data model & migration

- [x] 1.1 Add `EvaluationBackflowRuleModel` (name, task_id, dataset_id, filters JSON, limit, max_turns nullable, metadata_ last_run summary, workspace_id) + `EvaluationBackflowRuleCreateSchema` / `UpdateSchema` / `ReadSchema` in `src/hecate/models/evaluation.py`
- [x] 1.2 Add alembic migration creating `evaluation_backflow_rules` (new table only, no changes to existing tables)

## 2. Backflow service (`src/hecate/ops/evaluation/backflow/`)

- [x] 2.1 Promote dataset trace-id dedup to a shared helper reading both `metadata_.backflow.trace_id` and `metadata_.annotation.trace_id`; rewire `AnnotationService._dataset_trace_ids` onto it (no behavior change for the annotation path)
- [x] 2.2 Implement rule CRUD with validation: task exists and `task_type == online`, dataset exists in workspace, non-empty filters, `min_score <= max_score` per filter, positive `limit` (default 500) and `max_turns`, name uniqueness per workspace
- [x] 2.3 Implement candidate selection: `evaluation_task_scores` rows for the rule's task matching ALL score filters (per-metric band AND), ordered by `scored_at` ascending, capped at `limit`, with matched score snapshots fetched in the same pass
- [x] 2.4 Implement materialization: load trace, project via `build_eval_input` (trace window), map `EvalInput` → item per design D5 (multi-turn history into `metadata_.backflow.conversation_history` bounded by `max_turns`, `context` from `retrieved_contexts`, `expected_answer` empty, `tags=["trace-backflow", rule.name]`, provenance `metadata_.backflow` = trace_id/task_id/rule_id/scores/agent_id/session_id), skip candidates with no complete exchange or already materialized (either path), update rule `last_run` summary, return `{created, skipped, dataset_id}`

## 3. API layer (`ops/api` evaluation routes)

- [x] 3.1 Add `POST/GET /api/evaluation/backflow-rules` and `GET/PATCH/DELETE /api/evaluation/backflow-rules/{id}` with workspace scoping (cross-workspace reads return 404)
- [x] 3.2 Add `POST /api/evaluation/backflow-rules/{id}/run` returning `{created, skipped, dataset_id}`; validation errors 400/404 per existing evaluation-route conventions

## 4. Tests (`tests/`, in-memory SQLite + stub conventions per tests/AGENTS.md)

- [x] 4.1 Model + migration round-trip for the new table
- [x] 4.2 Rule CRUD validation matrix: offline task rejected, unknown task/dataset rejected, inverted score band rejected, empty filters rejected, non-positive limit/max_turns rejected, duplicate name rejected
- [x] 4.3 Run selection: score-band filtering (AND across filters), `scored_at` ordering, `limit` cap, zero-match run returns `{created: 0, skipped: 0}`
- [x] 4.4 Idempotency: rerun skips already-materialized traces; trace materialized via annotation path (`metadata_.annotation.trace_id`) is skipped by backflow run (and vice versa)
- [x] 4.5 Projection mapping: single-turn item content, multi-turn conversation history preserved (bounded by `max_turns`), oversize window skipped not truncated, `expected_answer` stays empty, `context` populated from retrieved contexts
- [x] 4.6 Provenance: `metadata_.backflow` carries trace_id/task_id/rule_id/score snapshot; tags include `trace-backflow` and rule name
- [x] 4.7 API integration: rule CRUD + run endpoint, workspace isolation (cross-workspace 404), error codes for invalid rule payloads

## 5. Verification

- [x] 5.1 `ruff check src/hecate/ tests/` + `ruff format --check src/ tests/` + `mypy src/` all clean
- [x] 5.2 `python -m pytest tests/ -q` full suite green (at minimum: evaluation, annotation, api scopes)
