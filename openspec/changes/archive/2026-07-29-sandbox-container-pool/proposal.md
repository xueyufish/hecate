## Why

The current sandbox execution model creates a new Docker container for every tool execution (`docker run --detach → docker wait → docker logs → docker rm`), resulting in 2-5 seconds of cold-start latency per execution. For agent workflows with frequent code execution (data analysis, code generation, iterative testing), this overhead dominates execution time. Pre-warming and reusing containers eliminates this cost — industry benchmarks show 10x improvement (LLM Sandbox, Polpo).

The existing `SandboxPool` class (`services/sandbox/pool.py`, 223 lines) has a fundamental design flaw: its `execute()` method allocates a container from the pool but then delegates to `SandboxExecutor.execute()` which creates an entirely separate container. The pooled container is never used for execution. This change fixes the pool and wires it into production.

## What Changes

- **Fix SandboxExecutor**: Add optional `container_id` parameter to `execute()` method. When provided, uses `docker exec` on an existing running container instead of `docker run --detach`. Single unified method, backward compatible.
- **Fix SandboxPool.execute()**: Use `executor.execute(..., container_id=container.container_id)` instead of `executor.execute(...)` so the pooled container is actually used.
- **Add production-grade pool features**: health check on acquire, TTL busy marker (crash recovery), idle trimming, per-workspace concurrency limit, exhaustion strategy (WAIT/TEMPORARY).
- **Wire into execution paths**: `builtin.py::_execute_code()` and `port.tool_execute_sandbox()` service adapter use SandboxPool when enabled.
- **Configuration**: New environment variables `SANDBOX_POOL_ENABLED` (default false), `SANDBOX_POOL_SIZE` (default 3), `SANDBOX_MAX_USES` (default 50), `SANDBOX_POOL_IDLE_TIMEOUT` (default 300s), `SANDBOX_POOL_ACQUIRE_TIMEOUT` (default 30s).
- **Lifecycle management**: Pool prewarms on application startup, shuts down on application shutdown (main.py lifespan integration).

## Capabilities

### New Capabilities

- `sandbox-container-pool`: Pre-warmed Docker container pool for sandboxed tool execution — prewarm, allocate, execute via `docker exec`, recycle (clean + return), retire at max-uses. Health check on acquire, TTL busy marker for crash recovery, idle trimming, per-workspace concurrency limit, configurable exhaustion strategy (WAIT/TEMPORARY). Disabled by default; opt-in via `SANDBOX_POOL_ENABLED=true`.

### Modified Capabilities

(none — existing `execution-security` spec requirements for tool approval and risk authorization are unchanged; pooling is a performance optimization layer below the security decision layer)

## Impact

- **Code**: `services/sandbox/pool.py` (fix + enhance), `services/sandbox/executor.py` (add container_id path), `services/tool/builtin.py` (wire pool), `engine/ports.py` + service adapter (wire pool into tool_execute_sandbox), `core/config.py` (new settings), `main.py` (lifecycle integration)
- **Tests**: Update existing `tests/test_services/test_sandbox/test_pool.py` (fix broken test that validated the design flaw), add tests for health check, TTL, idle trimming, exhaustion strategy, integration tests
- **Dependencies**: None new (Docker CLI already required for SandboxExecutor)
- **Performance**: Eliminates 2-5s container creation overhead per sandboxed tool execution; warm acquire <100ms
- **Security**: No change — pooled containers use same resource limits (CPU/memory/network/read-only FS) as per-execution containers; `/tmp` cleaned on recycle; `read_only_fs=True` restricts writable surface area
