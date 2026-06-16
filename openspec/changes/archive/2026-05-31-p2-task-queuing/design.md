## Context

The chat endpoint `POST /v1/chat/completions` processes messages asynchronously. When a user sends multiple messages rapidly to the same conversation, they are processed concurrently. This causes:

1. **Out-of-order processing**: Message B might complete before Message A
2. **Memory corruption**: Concurrent writes to L1 memory blocks or L3 user memory
3. **Context inconsistency**: L2 compression might race with new message processing
4. **Wasted resources**: Multiple LLM calls for the same session simultaneously

The existing rate limiter (`RateLimiter`) is per-API-key, not per-session. It doesn't solve the sequential processing problem.

## Goals / Non-Goals

**Goals:**
- Ensure sequential processing within a single conversation/session
- Queue new messages automatically when a session is busy
- Provide queue status feedback to the client
- Timeout queued messages after 5 minutes
- Minimal latency impact when no contention (no queuing overhead)

**Non-Goals:**
- Distributed queue (Redis/Celery) — single-server deployment only
- Cross-session queuing — each session is independent
- Priority queuing — strict FIFO only
- Persistent queue — in-memory, lost on restart

## Decisions

### D1: asyncio.Lock per session (not external queue)

**Decision**: Use `asyncio.Lock` keyed by session_id. When a message arrives for a busy session, await the lock with a timeout.

**Rationale**: Simple, no external dependencies, works for single-server deployment. The asyncio event loop handles fairness.

**Alternatives considered**:
- Redis queue — overkill for per-session ordering, adds dependency
- Database-backed queue — too much latency for chat messages
- Temporal workflow — planned for P3, not needed for simple ordering

### D2: Lock manager as singleton service

**Decision**: Create `SessionLockManager` as a singleton that manages `asyncio.Lock` instances per session_id. Locks are created on first use and cached.

**Rationale**: Centralized lock management, easy to test, clean separation of concerns.

### D3: Queue status via response headers

**Decision**: Return `X-Queue-Position` header (0 = processing, 1+ = queued position) and `X-Queue-Wait-Ms` header (time spent waiting).

**Rationale**: Non-breaking addition to existing API. Clients can poll or show status without changing the response body.

### D4: 5-minute timeout for queued messages

**Decision**: If a message waits more than 5 minutes in queue, return 408 Request Timeout.

**Rationale**: Prevents infinite waits if a processing message hangs. 5 minutes is generous for LLM responses.

## Risks / Trade-offs

- **[In-memory only]** → Queue state lost on server restart. Acceptable for chat messages (user can resend).
- **[Single-server only]** → asyncio.Lock doesn't work across processes. If scaling to multiple workers, need Redis-based lock.
- **[Lock contention]** → If one message takes 5 minutes, others wait. Mitigation: timeout prevents infinite waits.
- **[Deadlock risk]** → If lock is not released on error. Mitigation: use `async with lock:` pattern for automatic cleanup.
