# ADR-033: LLM-Managed Context Compaction via Surface Replacement

## Status

Accepted (2026-09-16; extends ADR-030 event-sourced execution state and ADR-032's log-as-plan discipline; specifies the durable half of 4.13 Context Engine Processor Chain)

> **落地状态（4.13 processor chain, 2026-09）**: the per-call projection half of 4.13 is implemented in `runtime/context_processors.py` (chain + processors + controlled termination). The **durable compaction backend** (`backend="surface_replacement"`) is implemented per this ADR's event schema, with two additive payload refinements recorded in the change design: `CONTEXT_SURFACE_REPLACED` acts as a projection-filter instruction (ledger entry) over an untouched channel — the "surface mutation" wording below reads as *the derived view mutates*, never the log or channel state — and its payload carries `start_anchor`/`end_anchor` (endpoint content hashes) alongside `start_seq`/`end_seq`, while `COMPACTION_SUMMARY` carries an optional `prior_compaction_id` (rolling re-compaction). Ledger ordinals are messages-channel message ordinals; the backend and message-channel eviction are mutually exclusive (assembly-time check).

## Context

The 4.13 processor chain projects conversation history per LLM invocation: tool-result truncation, window selection, offload, compression. All of these are **transient** — channel state and the event log (1.3.19 log-as-truth) stay untouched, and every invocation re-derives the projection from the full history. For long-running sessions this repeats O(N) projection work per call and never shrinks the fold input.

The planned enhancement (feature catalog 4.13, dsh CompactionEngine source-verified) is **LLM-managed compaction via surface replacement**: a compaction replaces a history *range* with one summary node while the original events stay in the log (shadowing); compaction events are themselves checkpoint sources. This is the durable, state-model-level counterpart of the transient compression processor — the same seam, two backends.

## Decision

Compaction is recorded as a **bracket event sequence** on the execution log. The summary node is materialized by the fold; the shadowed originals remain readable and replayable.

### Event schema (normative)

Four `EventType` values, additive (unknown-type reads fall back to `CUSTOM`, per the ADR-030 §1 contract):

| EventType | Payload | Fold semantics |
|---|---|---|
| `COMPACTION_STARTED` | `trigger` (`threshold` \| `manual`), `threshold_snapshot` (budget, tokens, window), `compaction_id` | Log-only. An unmatched `START` without its `COMPLETED` is an **in-progress lock**: a new compaction MUST NOT start for the session. Crash mid-compaction leaves an orphan `START` (visible, auditable), never a fabricated summary. |
| `COMPACTION_SUMMARY` | `compaction_id`, `summary` (structured node: objective, key decisions, current state), `shadowed_seqs` (inclusive seq range), `usage` | Log-only. Carries the summary node content and the reference to the shadowed source range. `shadowed_seqs` is what makes replay reconstructible. |
| `CONTEXT_SURFACE_REPLACED` | `compaction_id`, `start_seq`, `end_seq` | **The only surface mutation.** The derive-messages path (ADR-030 B2 log-derived recovery) substitutes the `COMPACTION_SUMMARY` node for the `shadowed_seqs` range. Channel fold: summary node enters the projected view; originals are never deleted from the log. |
| `COMPACTION_COMPLETED` | `compaction_id`, `tokens_before`, `tokens_after` | Closes the bracket. Log-only. |

Sequence per compaction: `START` → `SUMMARY` → `SURFACE_REPLACED` → `COMPLETED`. The `START`/`COMPLETED` pair acts as a **log-level distributed lock** — the `session/end-seed` stale-lock pattern (dsh) applies: a `START` followed by a newer session-end boundary without `COMPLETED` is stale, not busy.

### Trigger and execution

- Trigger: usage crosses `trigger_ratio × context window` (default 0.8), evaluated by the chain's token estimator at the `AGENT_PRE_STEP` waterline; `manual` via an operator/agent action. Overflow-error-triggered compaction (provider `CONTEXT_WINDOW_EXCEEDED`) is allowed to bypass the threshold.
- Summary generation: LLM call on the **routing-pinned summarizer route**; the summary request replays the byte-identical prefix (cache reuse) and is isolated from the tenant's main cache namespace.
- Ordering: the `ToolResultTruncationProcessor` runs **before** summary generation (pruning oversized tool results may obviate the LLM call entirely — dsh tool-result-pruner pattern).
- Retention: the most recent `retain_ratio` (default 0.16) of the window is never shadowed.
- Checkpoint interplay: a `COMPACTION_COMPLETED` event is a **checkpoint source** — resume folds from it cheaply (summary + post-compaction events), matching dsh `compactCheckpointSource` semantics.
- BUDGET_SNAPSHOT (4.10/4.13) records the `compress` level with `backend: "surface_replacement"` metadata for the invocation that triggered it.

### Interface (already reserved)

`CompressionProcessor(backend="surface_replacement")` — the processor seam exists in `runtime/context_processors.py`; the backend implementation registers into it. `1.3.15b` offload remains the recoverable tier *below* compaction: offload happens before shadowing so dropped blocks stay retrievable via `recall`/`read_file`.

## Consequences

**Positive:**
- Long sessions fold cheaply: resume and projection work on summary + tail, not the full log.
- Lossless audit trail: shadowed originals stay in the log; replay (8.20) can render pre-compaction state.
- The bracket lock makes concurrent/crashed compactions detectable without a coordinator.

**Negative / risks:**
- Time-travel to a point *inside* a shadowed range renders the summary node plus post-range events; pre-compaction detail requires replaying with shadowing disabled (explicit replay mode, documented in 8.20).
- Summary quality is lossy; the retention ratio and offload-below tier bound the damage.

## Seam registry (ADR-030 downstream consumers — new rows)

| Consumer | Seam | Extension pattern | Constraint |
|---|---|---|---|
| **4.13 durable compaction** (this ADR) | `EventType` enum (additive) + derive-messages path | Add the four compaction event types; `CONTEXT_SURFACE_REPLACED` participates in the derive-messages fold only (channel fold unchanged) | Bracket lock enforced; compaction MUST NOT start while a bracket is open; originals are never deleted |
| **8.20 Run Replay** | Consumer of `shadowed_seqs` | Replay UI renders compaction boundaries; pre-compaction replay = shadowing disabled | Compaction events MUST render as first-class timeline entries |

## References

- dsh `packages/compaction/` (source-verified): bracket events, `shadowedSeqs`, `surfaceOp.replace`, `compactCheckpointSource`, tool-result pruner before summary
- ADR-030 §Downstream consumer seam registry; ADR-029 (ContextEngine K-C; hot-path processors T0 behind SPI)
- Hermes `in_place` soft-archive and Codex `ContextCompactionItem` — convergent implementations of the same model
