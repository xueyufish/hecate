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
- **Prompt optimization never touches the production prompt** — the 6.19
  loop (`ops/prompt_optimization/`) rolls out candidate templates through
  `agent_execute(..., agent_definition.prompt_override)`; the agent's
  persona is bypassed during rollouts, its tools/KBs/model config are not.
  Rollout traces carry `metadata.optimization_run_id` / `candidate_id` so
  prompt analytics can separate optimization traffic from production.
  Candidates become real `PromptVersionModel` rows (with
  `metadata_.source="prompt_optimization"` provenance, no labels) only
  after explicit approval; the loop is off unless
  `PROMPT_OPTIMIZATION_ENABLED=true`.

## Citation provenance (1.3.5e Stage 1)

- **Markers live in channel state, raw content does not** — the ToolWorker
  marks tool results at the single choke point in `execute()` (after
  `asyncio.gather`), so the `【N-M】` prefixes are part of the persisted
  message content. There is no `original_content` field: the registry
  (`CitationProvenanceManager.registry_for(session_id)`) carries the chunk
  texts, and rebuilds from `CITATION_REGISTERED` events after restarts.
  Anything reading tool-result content programmatically must strip markers
  via `MARKER_PATTERN`.
- **Marker ids are bare in the registry, bracketed in text** — registry
  keys are `"N-M"`; response text and citation maps carry `【N-M】`. Lookups
  from scanned text must convert (`match.group(1)-group(2)`), not pass
  `group(0)`.
- **Uncited ratio is not a hallucination rate** — the risk signal counts
  factual sentences without any marker (D8 heuristic denominator). It says
  nothing about whether the cited chunks actually support the claim; that
  is Stage 2 (entailment scoring). UI/docs must not label it as
  hallucination detection.
- **Instruction message is a normal user message** — `[citation_instructions]`
  persists in history once and is re-appended transiently only when the
  projection dropped it (presence check in LLMWorker). Do not move it into
  the system prompt; the hints-never-touch-system-prompt rule applies.
- **Budget warn hints are a known trade-off, not an oversight** — Hermes
  deliberately removed context-pressure warnings because they caused
  models to give up prematurely on long tasks. `BudgetWarnProcessor` keeps
  them (spec'd behavior, once-per-crossing latch); if warn-hint artifacts
  show up in agent quality, revisit before adding more hint types.

## Grounding scoring (1.3.5e Stage 2)

- **Scoring is a heuristic, not a trust boundary** — the `GroundingScorer`
  backends (LLM judge / HTTP NLI) produce advisory verdicts recorded in
  `GROUNDING_SCORE` events. They never gate delivery: a contradicted
  response is delivered exactly as generated. Any future enforcement lives
  in the Stage 3 disposition layer and must be calibrated against the
  `would_block` shadow data, never turned on from the scoring layer.
- **Low confidence is never `contradicted`** — the red line in
  `grounding_scoring.py`: contradiction requires the backend to report
  explicit evidence disagreement. A binary-support NLI endpoint can never
  produce `contradicted` (its `false` maps to `unverifiable`). Do not
  "optimize" the mapping to treat weak support as contradiction.
- **`would_block` has no behavioral effect** — it is a shadow disposition
  (default thresholds are code constants; `shadow_thresholds` policy
  section can override for calibration). Do not wire UI or delivery logic
  to it until Stage 3 ships an opt-in disposition policy.
- **Scoring works with provenance disabled** — `_grounding_components`
  resolves the registry through the citation manager but tolerates its
  absence: every claim then goes through the fallback/unverifiable path.
  Enabling scoring without citation provenance gives much weaker evidence
  acquisition, not an error.
- **HTTP NLI endpoints are operator assets** — the platform never embeds a
  model runtime; `backend: "http_nli"` POSTs `(claim, evidence)` pairs to
  the configured endpoint (contract:
  `docs/operations/grounding-nli-endpoint-contract.md`). Timeouts degrade
  per-pair, never fail the invocation.
- **The LLM_RESPONSE chain now runs in LLMWorker** — `_finalize_response`
  executes the chain (legacy hook participates via its adapter) and appends
  the scoring stage per invocation on a fresh `Chain` copy; the shared
  chain object is never mutated. BLOCK/SANITIZE semantics are identical to
  the legacy direct-hook path (pinned by tests).

## Plugin namespace (dual format, 5.5d)

- **The namespace is `io.github.xueyufish`** — single source of truth is
  `AGENT_PLUGIN_NAMESPACE` in `core/plugin/dual_format.py`; the guard test
  (`tests/test_plugin/test_dual_format.py::TestNamespaceGuard`) pins the
  value. It is a **format identity, not configuration**: packages carry the
  same namespace in every deployment, so it must never become a config
  knob. Changing it invalidates already-distributed packages and requires
  an explicit migration decision.
- **Identity lives in plugin.json, not the namespace manifest** — the
  namespace `plugin.yaml` may repeat `name`/`version` only when they match
  plugin.json; a conflict rejects the package (replace-not-merge). The
  packaging tool emits the namespace manifest without either field.
- **The code payload lives inside the namespace directory** — entry modules
  resolve with `<package>/io.github.xueyufish/` on `sys.path`
  (`loader.load_namespace_plugin`). Root-level `discover_plugins` never
  picks up dual-format packages (no `plugin.yaml` at root), so the enable
  projection is the only load path — do not "fix" that asymmetry.
- **Dual-format packages land as `agent-plugin` rows** — legacy
  `.hecate-plugin` bundles (root `plugin.yaml`) keep the legacy installer
  path; layout detection at install routes between them. A dual-format
  package installed by a non-platform installer keeps its skills/MCP but
  the code component is skipped with a recorded warning (mirror of the
  stdio rule).

## Memory tools & recall (agent-memory-tools)

- **Memory tools fail closed on missing scope** — `memory_*` /
  `conversation_search` require `workspace_id` and `agent_id` in the tool
  execution context. The chat path and ToolWorker thread them
  automatically; a custom executor call site that omits them gets a
  `missing_scope` structured error, not an implicit-tenant fallback.
- **Memory tools need the backend wired at the construction site** —
  `BuiltInToolExecutor(memory_backend=...)` is only injected when
  `MEMORY_TOOLS_ENABLED` is on (chat registry builder and the MCP server).
  Without it the tools return `unavailable` instead of acting.
- **`conversation_search` is double-gated** — it seeds only when BOTH
  `MEMORY_TOOLS_ENABLED` and `RECALL_INDEXING_ENABLED` are on; searching
  with indexing off yields empty low-signal pages, not an error.
- **Recall `seq` is the event log version** — recall rows key on
  `(session_id, content_hash, seq)` with `seq = event.version`. Direct
  `index_messages` callers must pass `start_seq` aligned with the event
  versions, or the per-session watermark (`max(event_version)`) will
  re-index the same content under a different seq.
- **Recall outlives event retention by design** — retention prunes the
  `events` table only; recall rows survive until the owning conversation is
  deleted (cascade) or a workspace TTL is configured
  (`RECALL_TTL_DAYS`, 0 = indefinite).
- **`memory_update` dedup is not an audit event** — `memory_add` on
  near-identical content bumps `access_count` and reports
  `deduplicated: true`; only real mutations land in `memory_edit_log`.
