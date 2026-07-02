# ADR-005: Progressive Worker Pool for Distributed Execution

> **Status**: Accepted

## Context

The Pregel scheduler runs in a single process, but node execution (LLM calls, tool execution, code execution) can be slow and resource-intensive. The design needed to determine how to dispatch node execution without coupling the scheduler to a specific execution backend.

## Decision

Keep the **Pregel scheduler single-process** (lightweight) and dispatch actual node execution to a **Worker Pool**. The worker pool evolves progressively: in-process thread pool → cross-process workers → optional distributed backend.

## Rationale

Channel and Checkpoint ownership stays in the scheduler (simple, single writer). Workers are stateless and horizontally scalable (elastic). The evolution path allows starting with a simple thread pool and upgrading to distributed execution without changing the scheduler interface.

### Key Constraints

- Workers only receive Channel read-only snapshots — they never directly modify Channels
- Workers are unaware of Checkpoints — the scheduler uniformly persists after all workers complete
- Interrupts are triggered by workers via `WorkerResult.status = "interrupted"` — the scheduler handles the pause
- Workers are stateless — after restart, they can be rescheduled based on Checkpoint recovery

## Consequences

- The scheduler is the single source of truth for all state
- Worker implementations can range from threads to separate processes to distributed task queues
- Cross-process workers communicate via IPC or message queues
