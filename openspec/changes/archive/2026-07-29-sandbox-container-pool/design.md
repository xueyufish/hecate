## Context

The sandbox execution system (9.4c Docker Sandbox Executor) currently creates a new Docker container for every tool execution via `docker run --detach → docker wait → docker logs → docker rm`. This adds 2-5 seconds of cold-start latency per execution.

An existing `SandboxPool` class (`services/sandbox/pool.py`, 223 lines) was written to solve this, but has a fundamental design flaw: its `execute()` method allocates a container from the pool, then delegates to `SandboxExecutor.execute()` which creates an entirely separate container. The pooled container is never used.

This change fixes the pool and adds production-grade features based on industry research of 14 projects/platforms (LLM Sandbox, Polpo, E2B, Modal, Bedrock AgentCore, K8s agent-sandbox, container-pool, etc.).

## Goals / Non-Goals

**Goals:**

- Fix the fundamental SandboxPool.execute() bug — pooled containers must be used for execution via `docker exec`
- Add production-grade pool features: health check, TTL busy marker, idle trimming, exhaustion strategy
- Wire pool into both execution paths (builtin code execution + port.tool_execute_sandbox)
- Make pool opt-in with sensible defaults
- Zero behavior change when pool is disabled (all 2726 tests unaffected)

**Non-Goals:**

- Per-workspace physical container isolation (future enhancement; global pool with `/tmp` cleaning is sufficient for MVP)
- Distributed pool (Redis-backed multi-node) — single-process `asyncio` pool is sufficient for single-host Docker deployment
- Pool for DockerEnvironment (9.13 path) — that is per-agent persistent container, not a tool execution pool
- Container image customization per tool — single `hecate-sandbox:latest` image for all pooled containers
- Metrics/observability dashboard for pool — basic logging only; metrics endpoint deferred

## Decisions

### D1: Route A — Independent Pooling Layer (not merged into DockerEnvironment)

**Decision**: SandboxPool is a standalone layer on top of SandboxExecutor, not merged into DockerEnvironment.

**Rationale**: All 14 researched projects/platforms use independent pooling layers. None merge pool management into the execution environment. Key reasons: single responsibility, optional optimization, independent testability, backend replaceability.

**Alternatives considered**: Route B (merge pool into DockerEnvironment) — rejected because DockerEnvironment is per-agent persistent container while SandboxPool is global shared tool execution pool. They solve different problems and have different lifecycles.

### D2: Unified execute() method with optional container_id

**Decision**: SandboxExecutor.execute() gains an optional `container_id` keyword parameter. When provided, execution uses `docker exec` on the existing container. When omitted, execution uses the existing `docker run --detach` path.

```python
async def execute(
    self,
    tool_name: str,
    args: dict[str, Any],
    config: SandboxConfig | None = None,
    *,
    container_id: str | None = None,
) -> SandboxResult:
```

**Rationale**: Single API surface, backward compatible, caller doesn't need to know about two methods. LLM Sandbox uses this exact pattern (SandboxDockerSession connects to container_id, delegates operations).

**Alternatives considered**: Two separate methods (`execute()` + `execute_in_container()`) — rejected as unnecessarily expanding API surface. The distinction is an internal implementation detail.

### D3: Global pool scope with per-workspace concurrency limit

**Decision**: One global SandboxPool instance. All tool executions share the same pool regardless of workspace. Per-workspace concurrency limit prevents any single workspace from monopolizing the pool.

**Rationale**: E2B uses global pool with per-team concurrency reservations — global pool maximizes utilization, concurrency limits prevent starvation. Per-workspace physical isolation is over-engineering for self-hosted deployment with `read_only_fs=True` and `/tmp` cleaning.

**Alternatives considered**: Per-workspace pools — rejected due to container count explosion (workspaces × pool_size). Global pool + `/tmp` cleaning is sufficient given `read_only_fs=True`.

### D4: Default WAIT exhaustion strategy, configurable TEMPORARY

**Decision**: When pool is exhausted, default behavior is WAIT with 30s timeout. Configurable to TEMPORARY (create-and-destroy outside pool).

**Rationale**: Library projects (LLM Sandbox, container-pool, SQLAlchemy QueuePool) default to WAIT. Cloud platforms (Polpo, Modal, Bedrock) default to TEMPORARY because they have elastic resources. We are self-hosted with finite Docker capacity — WAIT is safer. 30s timeout matches SQLAlchemy and LLM Sandbox defaults.

### D5: Default disabled (opt-in)

**Decision**: `SANDBOX_POOL_ENABLED=false` by default. Users explicitly enable.

**Rationale**: LLM Sandbox, container-pool, and Modal all default to no pooling. First release should be conservative. Users with Docker who want performance opt in. Stable releases can flip default to true.

### D6: Health check on acquire via `docker exec <id> true`

**Decision**: Before handing a pooled container to the caller, verify it is alive by executing a no-op command. If dead, discard and try next or create new.

**Rationale**: Polpo's production blog documents this as essential — "A sandbox in the pool might be dead." Their pattern: acquire → isAlive → use or discard. LLM Sandbox does periodic + on-acquire checks. SQLAlchemy's `pre_ping` (SELECT 1) is the database equivalent.

### D7: TTL busy marker for crash recovery

**Decision**: When a container is allocated, record a timestamp. A background task checks for containers that have been in_use longer than 30 minutes (configurable) and force-releases them.

**Rationale**: Polpo uses 30-minute TTL busy markers with Redis SET. Prevents a crashed process from permanently removing a container from circulation — "a slow leak that eventually drains the entire pool."

### D8: Coexistence with 9.13 Sandbox Enforcement

**Decision**: SandboxPool and DockerEnvironment coexist without conflict.

- enforcement=true + shell tools → DockerEnvironment.exec_shell() (per-agent persistent container, unchanged)
- enforcement=true + code tools → SandboxPool (global pooled containers)
- enforcement=false + sandbox tools → SandboxPool

**Rationale**: DockerEnvironment and SandboxPool solve different problems. DockerEnvironment provides per-agent stateful execution (files persist across calls within an agent session). SandboxPool provides stateless isolated execution (cleaned between uses). They serve different tool types naturally.

## Risks / Trade-offs

**[State leakage between executions]** → Mitigation: `read_only_fs=True` restricts writable surface to `/tmp` only; `_clean_container()` removes all `/tmp` content on recycle; `docker exec` does not modify base environment variables.

**[Container dies while pooled]** → Mitigation: health check on acquire detects dead containers transparently; dead containers are discarded and replaced.

**[Process crash leaves container marked in_use]** → Mitigation: TTL busy marker (30 min) force-releases stale allocations; pool shutdown destroys all containers regardless of state.

**[Pool exhausts under high concurrency]** → Mitigation: WAIT strategy with 30s timeout prevents unbounded resource consumption; per-workspace concurrency limit prevents any single workspace from monopolizing; users can switch to TEMPORARY for elastic capacity.

**[Docker daemon unavailable]** → Mitigation: pool disabled by default; prewarm failures are logged but do not block startup; allocate falls back to on-demand creation which fails gracefully.
