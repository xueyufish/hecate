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
- **Skill discovery (5.9c) adds a governed third source** — when
  `SKILL_DISCOVERY_ENABLED=true` AND the workspace
  `settings.skill_discovery.enabled=true` (and the agent has not opted out
  via `skill_discovery_enabled=False`), eligible unbound workspace/bundled
  skills (`model_invocable`, non-`user` provider, plugin-enabled, above
  the workspace `min_trust_tier` floor) enter the L1 catalog and the
  `load_skill` advertised set. Discovery serves live rows — never
  ref_manifest pins: binding = frozen surface, discovery = live surface.
  Catalog overflow selection is deterministic: bound/auto_load first,
  then trust tier → provider rank → `skill_loaded` usage count → name.
  Usage events carry `detected_via` (`bound`/`auto_detected`); a
  discovered load whose `requires` cannot be resolved serves content plus
  a `dependency_warning` event — the 5.9e bind-time contract is
  unchanged. With the global switch off, the loader issues no extra
  queries (upgrade-neutral default).
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

## Skill provider registry (5.9-enh)

- **`provider` is not `origin`** — `SkillModel.origin` (String 1024) holds
  the plugin-package provenance (a git URL etc., set by 5.5c ingestion);
  `SkillModel.provider` is the rank-precedence classification
  (`bundled`/`user`/`project`; `custom` reserved). Plugin rows keep
  `provider = NULL` and stay outside rank competition on purpose.
- **Same-name rows now coexist — uniqueness moved with them** — the old
  `(workspace_id, name, deleted, deleted_at)` unique index was replaced by
  `(workspace_id, name, provider, deleted, deleted_at)` plus a partial
  unique index for `provider IS NULL` rows (keeps plugin one-row-per-name).
  Same-name cross-provider rows are legal; the 409 on create/import fires
  only for same name AND same provider.
- **Shadowing must go through `resolve_by_precedence`** — with multiple
  rows per name, "query and take the last row" silently changes meaning.
  Any new consumer of same-name candidates (loader, future 5.9d pinning)
  must resolve via `hecate.tools.skill.provider_registry`, whose order
  (project > user > bundled; unranked last) is the contract.
- **`auto_load` requires `model_invocable=true`** — enforced both at the
  schema level (create/update payloads) and in the update handler
  (final-state check catches flag changes that collide with a stored
  `auto_load=true`). The loader additionally filters `model_invocable=False`
  skills out of every model-visible surface, so drifted rows degrade to
  invisible rather than erroring.

## Skill versioning (5.9d)

- **Frozen snapshot, hashed subset** — `skill_versions.config_snapshot`
  freezes seven fields (`name` / `instructions` / `allowed_tools` /
  `scripts` / `references` / `description` / `max_tokens`); the
  `content_hash` covers only the 5-field set shared with agent-version
  reference manifests, so hashes recorded in the two places stay
  comparable. Snapshot integrity rests on row immutability, not the
  hash.
- **Plugin-sourced skills stay outside versioning** — `provider IS NULL`
  rows are rejected at commit with 409; the loader, in turn, never
  serves a plugin row from a pin (no version exists for it).
- **Rollback writes back, not just snapshots** — skills have no
  publish pointer; a rollback that only created a snapshot would be
  a no-op. `rollback_to_version` mirrors the snapshot to a new version
  *and* writes the frozen fields back to the live row, so the dirty
  badge zeroes immediately. Pinned agent snapshots keep resolving from
  the old version row.
- **Pinned agent manifests skip drift** — entries with `version` set
  on the reference manifest freeze their content, so `version_drift`
  skips them. Legacy snapshots with no `version` field keep drifting
  (resolved as unpinned).
- **Manifest skill entries must resolve via the provider registry** —
  `_build_ref_manifest` now calls `resolve_precedence_map` on the
  query result, so the skill pinned at commit time matches what the
  loader serves at runtime. Storage order decides nothing.
- **Loader overlay never mutates ORM state** — `SkillLoader._apply_pinned_content`
  builds a detached `SkillModel` overlay for each pinned skill; the
  session cannot flush frozen content back to the live row even when
  the live row is still present.

## Platform controller timer waits

- **Timer wait declarations are readonly-tool envelopes, never model output**
  — a task opts in via `controller.timer_wait`; only a settled, ledger-recorded
  readonly action may return `{"status": "timer_wait_required", "wait": {version, wake_in_seconds}}`.
  Model text and non-readonly tools keep the envelope inert (the loop just
  continues); the kernel JSON-round-trips the envelope in
  `_tool_result_content` exactly like `input_required`, so ledger replay
  stays parseable.
- **The parked deadline is receipt-anchored, and the waker re-derives it**
  — `_park_timer` anchors `wait_expires_at` on the declaring action's first
  durable receipt (plus the optional task-deadline clamp). A crash before
  the park commit re-parks with the identical deadline; `TimerWaitWaker`
  re-derives and parks for reconciliation on any mismatch instead of
  guessing a wake.
- **Timer wakes requeue, they do not terminate** — expiry consumes the
  token under the shared `expected_wait` commit gate (exactly one of
  {timer wake, cancel, manual command} lands), sets QUEUED, and the worker
  re-dispatches the SAME run; the accepted-decision chain replays the
  parked position. No input revision is recorded, so no-progress
  protection, turn budget and deadline keep their accumulated values.
- **Manual wakes cannot consume a timer wait** — `provide_input` on a
  timer wait is rejected ("expiry is the only wake"); the input/approval
  expirers ignore `wake_kind: "timer"` by construction.
- **Admission rejects unsupported timing rather than ignoring it** — invalid
  `controller.timer_wait` declarations fail before task creation. The opt-in
  requires a platform Controller task and the SQL action ledger; Runner and
  Workflow tasks reject it. A normal result without the opt-in remains data.
  Each accepted Controller decision binds the normalized timer policy.
- **The receipt remains authoritative** — wake verifies local platform ownership
  before any write, then checks the settled readonly action, its result digest,
  the original tool declaration and the receipt-anchored deadline. Missing
  tokens and inconsistent declarations require reconciliation. A copied or
  rewritten wait cannot replace its action receipt.
- **A wait needs an active reconciler** — the in-app durable worker includes
  `TimerWaitWaker`. Dedicated workers must use
  `hecate.execution.approval_wait_expiration:standalone_wait_reconcilers` as
  their `--wait-reconciler` factory. The durable state uses `waiting_input`
  with `wait.wake_kind: "timer"`; it does not request human input. Reading
  task details does not wake it. Restart recovery is tested in a fresh
  interpreter; this is not PostgreSQL concurrency certification.

For example, submit `controller_loop: true` with
`controller: {"max_turns": 8, "timer_wait": {"max_waits": 2}}`. A host-confirmed
readonly polling tool may return
`{"status": "timer_wait_required", "wait": {"version": 1, "wake_in_seconds": 30}}`.
The task can honor at most the declared number of waits across restarts. Once
the bound is exhausted, later envelopes stay ordinary tool data; the existing
turn, deadline, authorization and no-progress limits still apply. Timer expiry
only makes the task eligible for its next dispatch; it does not promise that
the business job completed or permit a new action after cancellation or expiry
of the task's own deadline.

## Platform controller Agent child calls

- **Execution scope** — the slice admits children whose targets carry
  effective tools: the delegation grant is the parent ∧ target rule
  intersection (deny-wins engine; the acceptance pre-check refuses tools
  either side denies or gates behind approval). Knowledge, skill and
  memory surfaces have no expressable intersection and stay refused.
  Delegated token-budget reservations are persisted (see the budget
  bullet); enterprise Team and cross-workspace targets are not. Do not
  treat the callback recovery tests as certification of the full
  Agent-to-Agent execution contract.
- **Delegation is a declared, host-owned tool** — a controller task opts in
  via `controller.agent_children` (aliases bound to same-workspace agents,
  optional `input_schema`/`output_schema` and per-target `max_calls`)
  plus a durable budget: `controller.agent_children.budget_units` (total,
  required with any per-target slice) and every target's `budget_units` are model-metered
  TOKENS, reserved against the workspace cost quota through the shared
  `MODEL_TOKEN_UNIT_SCALE`. Call counts are not a resource budget. The
  model sees `delegate_agent` with
  a declared alias enum. Child submission freezes its target execution
  snapshot; the callback verifies the settled call receipt, child input
  digest, deployment, ownership and declared acceptance standard.
- **Acceptance is idempotent by call identity** — the tool submits the
  child under `agent-child:<parent task>:<action execution id>`; settled
  acceptance replays its receipt. The park-commit crash test verifies the
  same child is reused. An unresolved submission still revalidates the
  current target before idempotent replay, so deployment changes in that
  window require further recovery work. `SubmitResult.task_ref` is a
  BackendRef — do not re-wrap it.
- **The budget loop is durable and non-resettable** — the child's slice
  ledger persists on the child's row (`budget_slice`, revision-CAS +
  dispatch lease): restarts, retries and fresh dispatches re-seed spent
  and unknown budget, and a retry's model attempt is refused BEFORE the
  model when the remaining slice cannot fund it (unknown attempts keep
  holding their full estimate — never charged as zero). The verified
  callback settles the workspace hold from that trusted record
  (idempotent per call identity; unknown holds stay held, proven-zero
  releases) and the wake accumulates the settled spend into the parent's
  cumulative `agent_child_budget_used`. Budget exhaustion refuses as
  model-visible data; a refused submission releases its proven-unexecuted
  hold. Release failures keep the action retryable. The reconciler also
  processes open delegation reservations after parent cancellation or
  expiry; cost settlement does not depend on waking that parent. Missing
  legacy metering retains an unknown allocation instead of declaring zero
  usage. Corrupt allocations or entries fail closed. Child budgets cover
  delegated model calls; parent model usage retains the normal workspace
  quota gate.
- **Responsibility chain** — the child keeps the parent's real initiator
  (`user_id` flows through); workflow children's `user_id=None` system
  identity was deliberately NOT copied. The child is one level deep: it
  cannot declare targets or be a workflow parent, and it always runs the
  durable controller chain — committed decisions replay after a crash,
  settled actions never re-execute, and the cooperative cancel observes
  a pending cancel command or a terminal parent before every turn.
- **The child result's truth source** — only the verified
  `submit_agent_child_callback` consumes an `agent_child` wait (manual
  `provide_input` is rejected); the wake records a durable
  `agent_child_result` input revision built from the child's committed
  facts. Successful delivery requires the shared output acceptance record
  bound to content, Task, Run, workspace and output schema; failed or
  cancelled children supply failure facts without successful content.
  `usage` remains `null` until a real usage integration reports.
- **Waker scope** — `AgentChildRunReconciler` (in both reconciler
  composites) recovers lost callbacks by re-issuing the deterministic
  callback and converges expired parent deadlines to
  `agent_child_deadline` (the still-queued child receives a durable
  cancel request — a request, not a stop). QUEUED children of a cancelled
  parent are cancelled outright. Dispatch entry also refuses inactive or
  expired parents. These checks do not interrupt a live model request.
  Indeterminate child state or corrupt wait bindings move the parent to
  reconciliation instead of leaving an unbounded wait.
- **Authorization boundary** — child submission requires the target's active
  agent principal. Targets with effective tools, knowledge, skills or memory
  configuration are refused until resource-level authorization is integrated.
  The declaration's exact `allowed_tools` narrowing alone cannot express the
  parent's resource scope. Runner rejects platform child declarations and
  external task input cannot set the internal `agent_child_parent` stamp.
- **PostgreSQL concurrency has its own matrix** — SQLite retains its own
  process-recovery and concurrency regression evidence. PostgreSQL row-lock
  and transaction behavior is verified separately:
  `tests/test_execution/test_agent_child_postgres_matrix.py` runs the
  competing executors as INDEPENDENT PROCESSES against `HECATE_STEP6_POSTGRES_URL`
  (skip, not pass, without it). The worker helpers bind their database through
  the child ENVIRONMENT (`_agent_child_db`), never argv: `str(engine.url)`
  masks the password, so an argv URL cannot authenticate a PostgreSQL child.
- **Stale executors are fenced, and the fence is observable** — a hung
  child executor's dispatch lease expires, the takeover requeues the task,
  and the hung executor's late write is refused with `presented fencing
  token N is stale ...; the write is rejected`. Its unsettled slice entry
  stays an unknown charge; the workspace hold settles as `unknown` (kept
  open, never charged as zero). A recovered child blocked at its dispatch
  entry by a cancelled parent is `failed` with `parent_cancelled` — the
  queued-at-cancel case is the one that becomes `cancelled`.
- **Concurrent deterministic callbacks must see "in flight", not a conflict** —
  the reconcilers reissue `agent-child-callback-<call>-<token>` with a fresh
  `issued_at` per submission. The recorder's one-content-per-id guard counts
  the timestamp, so a racing reissue raised "already recorded with different
  content" and the reconciler parked a HEALTHY parent for reconciliation.
  `issue_command` now detects its own concurrent recording (same intent,
  different stamp) and reports the current receipt. Effect metadata and
  receipt state may already have advanced; request identity still binds
  workspace, task, issuer, kind, payload, revision, expiry and callback data.
  An in-flight command can be retried without applying a completed one again.
- **Matrix scope is explicit** — provider responses and the `web_search`
  test effect are doubles. Two independent quota contenders are observed
  waiting on the real PostgreSQL row lock; slice writers synchronize after
  reading the same revision. Lease takeover waits for the stale process's
  model request before starting the successor and releases that model only
  after takeover completes. Deadline convergence uses an injected expiry
  clock; it does not certify physical clock skew or real-model quality.
- **Lost submission acceptance needs host evidence** — a created child whose
  caller died before settling `delegate_agent` is recovered from the immutable
  accepted action, exact submission association and verified child input,
  target and responsibility chain. The current parent dispatch lease fences
  confirmation in the ledger transaction, with an `action_outcome_confirmed`
  event. Rewritten input or mismatched identity stays in reconciliation.
  This confirms an existing submission; it neither creates another child nor
  relaxes replay policy for unresolved idempotent or external business writes.

## Workflow explicit controller steps

- **`mode: "controller"` runs a step as a REAL same-agent controller child** —
  the sequential workflow adapter (`workflow.steps`) accepts an explicit
  `mode: "controller"` (omitting it, or `chat`, keeps the historical
  single-turn execution). The step child carries
  `controller_loop: true` plus a controller block DERIVED from the
  validated step fields (`max_turns`, `deadline`) — a step cannot declare
  `agent_children` or its own `controller` block, so the workflow slice
  never mints further children and the model never widens its contract.
  The child rides the durable controller chain: accepted decisions replay
  after a crash, settled actions never re-execute, and `approval_wait`,
  `output_schema`, `artifact` and `on_failure` keep their existing
  semantics on the new mode. Only the host adapter may stamp the parent
  and step fields. A controller step retains the initiating identity and
  waits for its parent's matching durable wait before starting model work.
- **Budget and deadline declarations**
  `workflow.budget_units` (total) requires every controller step to
  declare its `budget_units` slice, and the slices must sum under the
  total (validated at submission). Each step's workspace hold is taken
  under `workflow-step:<parent>:<index>` BEFORE the child exists (a
  refusal leaves no child and no orphan hold; the reserve is idempotent
  per step identity), settles from the child's durable slice ledger at
  its verified callback (unknown usage stays held, proven-zero
  settles as zero), and accumulates into the parent's `workflow_budget_used`.
  The hold's lifecycle is fenced end to end: the child's creation commits
  only inside the parent's still-valid dispatch lease and an open step
  (`submit_task` verifies both in the creation transaction), and the
  reservation checks the same fence inside the quota scope transaction.
  Recovery takes that scope lock before reading or releasing holds, including
  scopes without a quota definition. A reservation already in flight must
  finish before recovery can acknowledge the close. When the parent converges terminal
  (cancelled / failed / succeeded) with a hold no child will consume, the
  relay's terminal-event handler runs the reclaim sweep: it closes the
  step on the parent's `workflow_step_states` extra marker and only then
  releases the hold — close-then-release, so a late reservation replay
  cannot mint a second hold, and an interruption between the two is
  repaired idempotently by the next sweep pass. A submitted child keeps
  its hold for settlement (the association is verified against the
  child's persisted stamp); an association read failure or a stamp/fact
  mismatch marks the step `held_for_reconciliation` and keeps the quota.
  Task detail exposes these markers as `workflow_step_states`. A competing
  marker writer cannot turn a reconciliation hold into a release. Failed
  marker writes and terminal attribution outages leave the outbox pending.
  Failed-submit cleanup also checks the dispatch lease under the quota
  scope lock so an old owner cannot release the new owner's hold. A parent parked `reconciliation_required`
  (restart budget exhausted, `on_failure: "await"`) keeps its holds
  visible until an operator resolves it; the sweep deliberately does not
  act there. Late dispatch into a closed step is refused with
  `failure_source: "closed"`.
  Terminal settlement still runs when a cancelled parent cannot wake.
  The workflow deadline gates advances and caps each controller step's
  own deadline and approval or input wait. Refusals
  are distinguishable (`failure_source`: `budget` / `deadline` / `closed`
  vs `execution` / `validation`).
- **Parent cancel records a cancel request on the held step child** —
  cancelling (or converging) a parent parked on `await_task_ref`
  persists a real CANCEL command (`workflow-cancel:<parent>:<child>`) for
  the bound controller child. The durable state event lets the outbox
  retry propagation if the request process dies after the parent commit.
  A QUEUED child
  is cancelled inline, a RUNNING child stops cooperatively through its
  own dispatch rules, and a late verified callback cannot re-activate
  the terminal parent.
- **Lost step callbacks recover through the outbox, racing safely** — a
  child that reached terminal with its callback lost is recovered by the
  relay's redelivery of `_issue_child_callback` (the same verified
  `submit_workflow_callback` path, idempotent per parent+step via the
  `_workflow_callbacks` receipt and the single-use wait token). A child
  that needs reconciliation parks its bound parent through the durable
  state event, without creating a successful callback.
  `tests/test_execution/test_workflow_controller_postgres.py` proves the
  death window, the two-process callback race (a ready-file barrier puts
  both racers at the boundary before either fires), the cross-process
  cancel propagation and the reservation-recovery barrier (a paused
  executor's late submission is refused after the takeover's sweep closed
  the step, with no child and no re-opened hold) against
  `HECATE_STEP6_POSTGRES_URL` — skips, not
  passes, without it; `test_workflow_controller_steps.py` holds the
  SQLite contract tier.
