# ADR-003: Checkpoint Persistence with Memory Cache

> **Status**: Accepted

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
