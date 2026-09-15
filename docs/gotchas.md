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

## Intent recognition & packages (6.23/6.49/2.6a)

- **Runtime never reads draft intent content** — evidence flows only from
  *published* package versions through the injected `IntentEvidencePort`
  (composition-root provider `core/composition/intent_evidence.py`). A
  version pin resolves to that exact published row; an unpinned reference
  resolves to `latest published`. If the port raises, recognition degrades
  to the fallback-label LLM path and marks `evidence_available=false` — it
  does NOT fail the turn.
- **Decision cache keys bind the evidence version** —
  `(normalized_utterance, package_version_id, context_fingerprint)`.
  Publishing a new version rotates the key space; there is no invalidation
  broadcast (by design). Do not add cache entries for failed
  classifications — a fallback decision must not pin the failure for the
  TTL.
- **`_intent_state` is a real channel** — the controller persists session
  intent via channel writes; reserved underscore channels are silently
  SKIPPED if not declared. `build_base_chat_graph` declares
  `_intent_state` (LAST_VALUE); graphs built outside that helper must
  declare it too, or sticky routing silently degrades to per-turn
  recognition with no goal memory.
- **Deterministic freeze ordering uses `position`** — rows created in one
  transaction share `created_at` (PostgreSQL `now()` is transaction-
  scoped), so (created_at, id) ordering is NOT deterministic for the
  content hash. Categories/samples carry an explicit `position`;
  hash projections must preserve it.
- **publish gate reads deterministic signals only** —
  `latest_recognition_result` filters `source="deterministic"`; feeding it
  LLM-judge or human scores is a design violation, not a rounding issue.
- **No few-shot payload or raw utterance in events** — `INTENT_RECOGNIZED`
  carries the utterance *fingerprint* and the evidence *reference*; putting
  payloads into the event would leak package content into traces.
- **Skill loading is two-level (progressive disclosure, 1.3.6f)** —
  `SkillLoader.format_skills()` returns an L1 catalog (name + description
  per bound skill); full instructions (L2) are served on demand via the
  `load_skill` built-in tool, which requires `agent_id`/`workspace_id` in
  the tool context (chat path and ToolWorker thread them). Skills with
  `auto_load=True` keep full injection and are excluded from the catalog.
  Set `SKILL_PROGRESSIVE_DISCLOSURE=false` to restore the legacy
  inject-everything behaviour.
- **Learned skills are knowledge-only and human-gated** — candidates from
  the evolution loop (`studio/self_evolution/`) are published as
  `SkillModel` rows with `source="learned"` + provenance
  (`learned_run_id`, `failure_category`) only after an explicit review
  decision; generation rejects any draft containing fenced code blocks.
  The loop is off unless `SKILL_EVOLUTION_ENABLED=true`.
