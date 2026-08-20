# ADR-030: Event-Sourced Execution State (Log-as-Truth)

## Status

Accepted (2026-08-15; extends ADR-001 engine foundation, ADR-013 distributed session state store; sequenced first in Sprint 7 per the 2026-08-14 re-scope)

## Context

The 2026-08-14 competitive analysis (`docs/research/2026-08-competitor-analysis.md`) and the dsh source code analysis (`docs/research/2026-08-deepseek-harness-analysis.md`) converged on a single architectural gap: **Hecate's event log is an observation, not the source of truth.** The day-to-day evidence in `src/hecate/engine/pregel.py` and `src/hecate/engine/eventstore.py` confirms three layered deficits:

1. **Snapshot cost (O(N²))**: `PregelRuntime` saves a full `channel_state` snapshot per superstep (`pregel.py:407-412`). For long conversations the same channel payload is re-serialized every step.
2. **WAL observability is fake**: `CHANNEL_WRITE` events only log channel *names* (`pregel.py:399`), not values. The log cannot reconstruct state — it has zero readers (`grep get_events` finds no production callers).
3. **Cross-process durability is half-implemented**: `execution_service.py:338` constructs `InMemoryCheckpointStore()` per request; the resume endpoint (`api/management/sessions.py:134-181`) only flips a status bit and drops `resume_value`. `SessionState.channel_state` is always an empty dict (`execution_service.py:530`). 13.4a built the *seams* (event table, `SessionStateStore`, `event_position` cursor) but never *wired* them through to chat path B.

Meanwhile the downstream feature queue locks on this dependency:
- **1.3.4 HITL fail-closed** needs durable audit pairs in the log.
- **1.3.5i E3 waterfall middleware** needs an event-shaped interception seam.
- **9.4 content-aware gating** needs the monotonic-denial invariant substrate.
- **8.20 Run Replay** needs a value-carrying, boundary-anchored log to replay.
- **8.21 Projection Registry** needs `derive_messages()` as a first-class projection function.

All five are sequenced after 1.3.19 in Sprint 7. The roadmap (`docs/features/roadmap.md` Sprint 7 ordering note) is explicit: **1.3.19 must land first.**

## Decision

The event log becomes the execution-state source of truth. The runtime gains three enforcement mechanisms so the invariant holds in practice, not in spec.

### 1. Event-log syntax (two layers)

* **Channel-delta layer (general)**: `CHANNEL_WRITE {channel, value, log_schema_version}` carries the **post-adjudication** value. `CHANNEL_WRITE_REJECTED` is the audit-only companion; fold skips it.
* **Session-semantic layer**: `STEP_END` / `EVICTION` / `SUBGRAPH_START {child_session_id}` / `SUBGRAPH_END {child_session_id}` / `INTERRUPT {interrupt_value}` / `RESUME` / `LLM_REQUEST {frozen}` / `LLM_RESPONSE`.

Existing `EventType` enum is extended (additive, unknown values fall back to `CUSTOM` on read). `CURRENT_LOG_SCHEMA_VERSION = 2`.

### 2. Write-ahead ordering + commit points (WAL)

In each superstep: result collection → conflict adjudication → **single-transaction batched `append_batch` of channel-delta events + `STEP_END`** → channel apply. Append failure fails the superstep (no partial channel state). `STEP_END` and `INTERRUPT` are commit points; a torn tail (`CHANNEL_WRITE` without a commit event after it) is rolled back to the last complete superstep on restore.

This is one DB round-trip per superstep (replacing today's per-superstep full snapshot). Eviction events drain into a separate batch after apply (their order does not need a STEP_END anchor).

### 3. Projection cache + three enforcement mechanisms (the three teeth)

`ChannelManager` keeps its hot-path role (memory projection); the canonical write path is now `fold ← log`, not `read ← cache`. The cache is an **optimization**, not an authority. Three enforcement mechanisms ("teeth") keep the invariant honest:

| Tooth | Mechanism | Failure mode |
|---|---|---|
| **WAL** | Batch append before channel apply; commit points anchor restartable points | Append failure → no apply (consistent with today's per-superstep checkpoint atomicity) |
| **Single fold function** | `fold_session` uses the registered `ChannelBehavior.write`; same code as live mutation | Two implementations diverging — prevented by construction |
| **Boundary equivalence assertion** | On restore / interrupt / materialization: fold full log, compare to `ChannelManager` snapshot (non-LogPolicy channels only); mismatch = `RuntimeError("[PROJECTION.EQUIVALENT] ...")` | In-process state divergence is a bug; recovery is "discard + re-fold from log", not hot-fix (the latter masks the bug and breaks object identity with worker-held deep copies) |

The cache itself shrinks to `channel_state + log_version`; `superstep` / `interrupted_node` / `interrupt_value` / route are derived from log events on restore.

### 4. LogPolicy (default-in + blacklist)

Channels are logged by default. A single `engine/logpolicy.py` registry excludes three classes:

1. **Ephemeral structural intermediates** — `_fanout__*`, `_resume_value`
2. **Re-injectable control channels** — `_`-prefixed and `sys.`-prefixed (services re-inject per request)
3. **Channels with dedicated persistence** — `_agent_state` (live object stored via SessionStateStore)

`_route` is explicitly **included** (conditional-edge routing is part of fold correctness). Exclusions live in one place to prevent drift.

### 5. LogInvariants registry (dsh companion-module pattern)

`engine/loginvariants.py` registers runtime checks via decorator, invoked during fold. Shipped checks: `STEP.BOUNDARY`, `TOOL.PAIRING` (pending tool calls cannot cross STEP_END), `DISPATCH.TREE` (every `SUBGRAPH_START` has matching `SUBGRAPH_END`). LLM-request provenance check + projection-equivalence assertion are future additions (8.20 Phase 2 needs version binding for full reconstruction; today we ship provenance).

### 6. Subgraph nested sessions (C1)

Sub-graphs use independent `session_id`. The parent log records `SUBGRAPH_START {child_session_id}` / `SUBGRAPH_END {child_session_id}`. Parent fold is zero-filter clean. Subgraph runs are independently replayable (essential for multi-agent debugging). Retention cascades across the tree.

### 7. Resume (B2)

The resume endpoint validates against the log (an unclosed `INTERRUPT` event in `get_events` is the only precondition). `SessionModel` row is lazy-created if missing. `SessionModel.status` is a projection: any direct write is invalid; only the engine (via log events) advances it.

### 8. Lifecycle / retention (B1)

Session-level TTL counted from terminal state (completed/failed/expired). `interrupted` sessions are exempt. Default conversational 30d / task 7d (industry consensus: Temporal Cloud 30d, OpenAI Responses 30d, Bedrock AgentCore 14d). `RetentionConfig` carries `delete | archive` enum; `archive` is reserved for a future change (no implementation this round). Single-session warn thresholds (10MB / 10k events) emit metric only.

### 9. CheckpointStore demoted to materialization seam (C2)

`CheckpointStore` ABC kept (rename churn avoided); production saves go through `SessionStateMaterializer` (services/orchestration/), which writes through the existing `SessionStateStore` (Redis / PostgreSQL / Tiered) under the tenant-scoped `(org_id, user_id, session_id)` triple. Tenant context injected via `tenant_context_provider` closure (same pattern as `PostgresEventStore`). `PostgresCheckpointStore` soft-deprecated (DeprecationWarning + module docstring), hard removal deferred to a follow-up cleanup change (along with `checkpoints` table drop), per the 13.4a-6 / 13.4a-7 two-stage precedent.

### 10. Path A / Path C coverage boundary (non-goal)

`_chat_with_tools` (agent-tools direct loop, engine-tool-loop change 2026-08-13) and the pure-text passthrough path do NOT route through `PregelRuntime` and therefore emit no events. Log coverage = Pregel execution path only. The non-Pregel paths carry explicit TODO pointers pointing at `docs/design/engine-design.md`. Long-term convergence: model the tool loop as an engine-internal subgraph (paired with 1.3.5i E3 waterfall). Recorded as roadmap debt.

### Downstream consumer seam registry (for 1.3.4 / 1.3.5i E3 / 9.4 / 8.20 / 8.21)

This section is the **handoff contract** for the five consumers sequenced after 1.3.19. Each knows what to extend without re-reading the engine.

| Consumer | Seam | Extension pattern | Constraint |
|---|---|---|---|
| **1.3.4 fail-closed approval** | `EventType` enum + `EventType` falls back to `CUSTOM` on unknown read | Add `APPROVAL_ASKED` / `APPROVAL_DECIDED` values (additive; old readers keep working) | Audit pair SHALL be `TURN_START` / `TURN_END` enclosed |
| **1.3.5i E3 waterfall middleware** | Semantic-layer event schema + `LogInvariants` registration | Incrementally add stage event types; no fold change needed | Chain semantics (ordering, short-circuit, monotonic denial) live in **kernel** — middleware stages are not pluggable chain mechanism (see ADR-029) |
| **9.4 content-aware gating** | `CHANNEL_WRITE_REJECTED` pattern + `LogInvariants` registry | Register a `MONOTONIC.DENIAL` invariant; emit `CHANNEL_WRITE_REJECTED` for denied writes | Guards SHALL be monotonic (deny only); resurrection is a bug, fail-stop |
| **8.20 Run Replay** | Pure consumer — reads `get_events` + OTel `TraceModel` JOIN | None (zero schema change required) | Coverage = Pregel path; UI MUST label "path A/C calls not in log" |
| **8.21 Projection Registry** | `derive_messages()` is the first projection function | Build the registry around `fold_session` semantics; no fold change needed | Projections SHALL be pure functions of `(log, config)`; Phase 1 cannot do full reconstruction (config version binding is Phase 2 of 8.20) |

### Consequences

**Positive:**
- O(N²) → near-linear storage (delta writes + checkpoint caches; full snapshots disappear)
- Cross-process resume becomes feasible (event log + materialization cache, not snapshot injection)
- The five consumers (1.3.4 / 1.3.5i E3 / 9.4 / 8.20 / 8.21) gain a stable extension surface; no engine surgery required for their incremental work
- Multi-agent debugging becomes possible (independent subgraph replay)
- GDPR cascade has a single authoritative source (log + cache pair)

**Trade-offs (acknowledged):**
- Each superstep now has one synchronous DB round-trip for `append_batch` (down from per-superstep full snapshot which is strictly more bytes; net win for chat-scale). Async batching is a future implementation-time decision.
- Log schema is no longer backward-mutable (any pre-`log_schema_version=2` events are unreplayable). Mitigation: zero production wiring of the old `InMemoryCheckpointStore`-based path means the migration surface is essentially empty.
- Path A / C coverage gap remains until the engine-tool-loop consolidation lands.
- Conversation / Message model is **deliberately not unified** with the log in this change (A2 debt); the log is authoritative for engine state, conversation tables remain a UI projection.

## References

- Source analysis: `docs/research/2026-08-deepseek-harness-analysis.md` (dsh source code, v0.1.0-rc.5)
- Competitive analysis: `docs/research/2026-08-competitor-analysis.md` (18 platforms + 6 new entrants, re-verification)
- Related ADRs: ADR-001 (engine foundation), ADR-013 (distributed session state store), ADR-016 (platform SPI), ADR-029 (trust-tiered kernel/plugin)
- OpenSpec change: `openspec/changes/event-sourced-state/`
- Engine implementation: `src/hecate/engine/{eventstore,pregel,channel,checkpoint,logpolicy,logfold,loginvariants,session_state}.py`
- Services adapter: `src/hecate/services/orchestration/session_state_materializer.py`
- Retention: `src/hecate/services/retention/event_retention_service.py`
