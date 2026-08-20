# ADR-003: Checkpoint Persistence with Memory Cache

> **Status**: ~~Accepted~~ **Superseded by [ADR-030](030-event-sourced-execution-state.md)**
>
> Per ADR-030 (Log-as-Truth), the **event log is the single source of truth** for execution state. The PostgreSQL checkpoint described below is **demoted to a discardable materialized cache** (`channel_state + log_version`); execution is recovered by replaying the event log, not by loading a checkpoint snapshot. `PostgresCheckpointStore` is soft-deprecated, pending hard removal (and `checkpoints` table drop) as a follow-up cleanup. This ADR is retained for historical context.

## Context

Hecate needed to determine Session state management strategy — stateless (rebuild from event log), stateful (in-memory only), or hybrid.

## Decision

Implement **Checkpoint persistence to PostgreSQL** as the primary state store, with an **in-memory cache** for hot-path acceleration.

## Rationale

The Checkpoint interface must exist from the start to support breakpoint recovery and time-travel debugging. Pure statelessness (rebuilding from event sourcing on every request) adds latency. Pure in-memory state risks data loss on restart.

The hybrid approach writes every superstep to PostgreSQL (durable) and caches the most recent checkpoint in memory (fast). On session resume, the system loads from the cache if available, falling back to PostgreSQL. Database writes can be asynchronous (WAL first, background flush) to avoid blocking the execution loop.

## Consequences

- Every superstep produces an immutable checkpoint
- Checkpoints are never modified, enabling time-travel debugging
- Cache consistency is managed by the scheduler (single writer)
