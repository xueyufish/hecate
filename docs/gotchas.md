# Engine & model gotchas

Non-obvious behaviors that are not apparent from reading the code in a few
minutes. Environment/git gotchas live in the root `AGENTS.md`.

## ORM / Pydantic aliases

- **AgentModel.model_config_db** — ORM column `model_config` via
  `mapped_column("model_config", JSON)` (avoids Pydantic `model_config`
  collision). CreateSchema `alias="model_config"`, ReadSchema
  `serialization_alias="model_config"`.
- **metadata_ alias** — 11 models across 10 modules map `metadata_` (Python)
  → `metadata` (SQL); ReadSchema uses `Field(validation_alias="metadata_")`.

## Runtime engine semantics

- **compiler._detect_unreachable()** uses BFS from entry; logs WARNING for
  unreachable nodes (does not raise).
- **ChannelManager**: `write()` silently skips unregistered channels;
  `read()` raises KeyError (`ChannelNotFoundError`); `restore()` bypasses
  write semantics and sets `_value` directly.
- **StreamMode** has only VALUES, UPDATES, MESSAGES — DEBUG no longer exists.
  MESSAGES is the SSE streaming mode.
- **PERSISTENT_TOPIC** channel type is deprecated — auto-migrated to `topic`
  + `persistent: true` by `studio/workflows/graph_dsl.py` (formerly
  `services/workflow/graph_dsl.py`).
- **Guardrail wiring** — hooks and middleware chains are assembled by
  `src/hecate/runtime/security/guardrail_assembly.py` (formerly
  `services/security/`), on both the Pregel path and the
  `channel/api/v1/chat.py` direct tool loop.

## Type checking

- **mypy** runs `strict=true` but many error codes are disabled in
  `pyproject.toml` — not truly strict. Do not assume a passing mypy run means
  full strict coverage.

## Evaluation scores (7.4/7.4a)

- **evaluation_task_scores is a shared ledger** — automated rows carry
  `task_id`, human annotation rows have `task_id=NULL` (+ `source="human"`,
  `annotator_id`). Idempotency is a *partial* unique index
  (`WHERE task_id IS NOT NULL`), so multiple human rows for the same
  target+metric are legal; the service upserts per
  `(annotator_id, target_id, metric_name)` instead of relying on the DB.
- **Categorical annotations** store the category string in `value_label`
  and its index in `value` (the column is Float) — dashboards wanting the
  label must resolve via the queue's `metric_defs.categories`.
- **Report reconciliation** — overview/trends/distributions/session-rollup
  aggregate *reconciled* values: the latest human override
  (`overrides_score_id`) replaces the superseded automated row, and the
  override's `created_at` is the effective time (its trend bucket). The
  breakdowns endpoint is deliberately raw (`group_by=source` shows both).
