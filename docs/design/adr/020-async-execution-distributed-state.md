# ADR-020: Asynchronous Execution and Distributed Session State

> **Status**: Proposed
> **Date**: 2026-07-01

## Context

Hecate's execution engine currently supports two execution modes: synchronous (blocking HTTP) and streaming (SSE). While these cover most interactive agent scenarios, enterprise workflows — such as quarterly report generation, multi-round deep research, and batch document processing — may run for minutes to days. The synchronous mode risks client-side timeouts, and the streaming mode requires the client to maintain a persistent connection throughout.

Additionally, Hecate's checkpoint persistence stores session state in PostgreSQL. While this provides durability, it is too slow for hot-path session state access during multi-replica horizontal scaling (feature 13.4). Competitors like AgentScope 2.0 use Redis-backed state stores (`RedisAgentStateStore`) enabling any replica to pull any session's full state in sub-millisecond, achieving true stateless horizontal scaling.

## Decision

Add **two execution model enhancements**:

### 1. Asynchronous Execution API Mode (1.3.11)

A third execution mode for long-running workflows:

```
Client → POST /api/workflows/{id}/execute
         → Returns: { task_id, status: "submitted" } immediately
         → Workflow executes in background

Client → GET /api/tasks/{task_id}
         → Returns: { status: "running" | "completed" | "failed", result: ... }

Client → DELETE /api/tasks/{task_id}
         → Returns: { status: "cancelled" }
```

| Aspect | Sync | Streaming | Async (NEW) |
|--------|------|-----------|-------------|
| Client connection | Blocking | Persistent SSE | Fire-and-forget |
| Duration limit | 60s | 15min | 24h+ |
| Result delivery | HTTP response | SSE stream | Poll or webhook callback |
| Cancellation | Close connection | Close SSE | DELETE task_id |
| Use case | Quick Q&A | Real-time chat | Batch processing, reports, research |

Task lifecycle: `submitted → running → completed/failed/cancelled`.

Implementation: The async mode wraps the existing Pregel runtime in a background task runner (asyncio Task or Celery worker). The engine itself is unchanged — only the API layer and session lifecycle management differ.

### 2. Distributed Session State Store (13.4a)

A Redis-backed hot-path cache layered on top of PostgreSQL checkpoints:

```
Session State Access Flow:
    1. Check Redis cache (sub-millisecond)
    2. Cache miss → Load from PostgreSQL checkpoint (durable)
    3. Cache in Redis with TTL
    4. Any replica can serve any session — no sticky sessions

Write Path:
    1. Every superstep → PostgreSQL checkpoint (durable, immutable)
    2. Latest checkpoint → Redis cache (hot-path, mutable, TTL-based eviction)
```

The store uses a `SessionStateStore` ABC (similar to `CheckpointStore`) with two implementations:
- `InMemorySessionStateStore` — Development (dict-based, no external dependency)
- `RedisSessionStateStore` — Production (Redis-backed, sub-millisecond reads)

## Rationale

### Asynchronous Execution

- **Client simplicity**: Fire-and-forget is simpler than maintaining a persistent SSE connection for hours
- **Resilience**: Network interruptions between client and server don't kill the workflow
- **Scalability**: Background tasks don't consume HTTP connection pool slots
- **Industry precedent**: Coze (3 modes: sync/streaming/async), Dify (Celery worker-based async)

### Distributed Session State

- **Horizontal scaling**: Any K8s replica with HPA can serve any user — no sticky sessions
- **Performance**: Redis reads (~0.5ms) vs PostgreSQL reads (~5ms) for hot-path state
- **Durability preserved**: PostgreSQL remains the source of truth; Redis is a cache
- **Industry precedent**: AgentScope 2.0 (RedisAgentStateStore), Huawei AgentSphere (sandbox snapshot → Redis)

## Consequences

- Feature 1.3.11 is P4 (Sprint 6) — depends on streaming ✅ + session management ✅
- Feature 13.4a is P3 (Sprint 5) — prerequisite for 13.4 Horizontal Scaling
- Redis becomes an optional infrastructure component (required for horizontal scaling, not for single-instance deployment)
- The async execution API endpoint (`POST /api/workflows/{id}/execute` + `GET /api/tasks/{task_id}`) is a new API surface addition, not a change to existing endpoints
- Task lifecycle management (submitted→running→completed/failed/cancelled) aligns with A2A task lifecycle (ADR-011)
