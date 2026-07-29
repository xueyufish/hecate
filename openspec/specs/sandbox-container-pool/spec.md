## ADDED Requirements

### Requirement: SandboxExecutor docker exec support
The system SHALL extend `SandboxExecutor.execute()` with an optional `container_id` keyword parameter. When `container_id` is provided, the executor SHALL run the tool inside the specified existing container via `docker exec`, returning stdout/stderr/exit_code directly. When `container_id` is omitted, the executor SHALL use the existing `docker run --detach → docker wait → docker logs → docker rm` path (backward compatible).

#### Scenario: Execute in existing container
- **WHEN** `execute(tool_name, args, config, container_id="abc123")` is called
- **THEN** the executor runs `docker exec abc123 <command>` and returns a `SandboxResult` with the execution output
- **AND** the container `abc123` remains running after execution

#### Scenario: Execute without container_id (backward compatible)
- **WHEN** `execute(tool_name, args, config)` is called without `container_id`
- **THEN** the executor creates a new container via `docker run --detach`, executes, and destroys it (existing behavior)

#### Scenario: docker exec timeout
- **WHEN** a `docker exec` call exceeds the configured timeout
- **THEN** the executor terminates the exec process and returns a `SandboxResult` with `timed_out=True`
- **AND** the container itself is NOT destroyed (it is managed by the pool)

### Requirement: SandboxPool execute uses pooled container
The system SHALL fix `SandboxPool.execute()` to pass the allocated container's `container_id` to `SandboxExecutor.execute()`, ensuring the pooled container is actually used for tool execution.

#### Scenario: Pool execute uses pooled container
- **WHEN** `pool.execute("execute_code", {"code": "print(1)"})` is called
- **THEN** the pool allocates a container, executes the tool inside that container via `docker exec`, and recycles the container
- **AND** the executor does NOT create a separate container

### Requirement: Pool pre-warming on startup
The system SHALL pre-create `SANDBOX_POOL_SIZE` containers (default 3) when the pool is initialized, each running `sleep infinity` to stay alive and ready for tool execution.

#### Scenario: Prewarm creates configured number of containers
- **WHEN** the pool is initialized with `pool_size=3`
- **THEN** 3 Docker containers are created with resource limits matching `SandboxConfig`
- **AND** each container runs `sleep infinity` as its entrypoint

#### Scenario: Prewarm partial failure continues
- **WHEN** one container fails to create during prewarm
- **THEN** the pool logs a warning and continues with the remaining containers
- **AND** the pool supplements on demand when allocate is called

### Requirement: Health check on acquire
The system SHALL verify that a pooled container is alive before handing it to the caller. The health check SHALL execute a no-op command (`docker exec <id> true`) and verify exit code 0. If the container is dead, it SHALL be discarded and the pool SHALL try the next container or create a new one.

#### Scenario: Healthy container allocated
- **WHEN** `allocate()` is called and the first available container passes health check
- **THEN** the container is marked in_use and returned to the caller

#### Scenario: Dead container detected and replaced
- **WHEN** `allocate()` is called and a container fails health check
- **THEN** the dead container is removed from the pool and destroyed
- **AND** the pool tries the next available container or creates a new one

### Requirement: Container recycling with state cleanup
The system SHALL clean container state after each use by removing all files in `/tmp`. After cleaning, the container SHALL be marked available for reuse. If the container has reached `SANDBOX_MAX_USES` (default 50), it SHALL be destroyed instead of recycled.

#### Scenario: Recycle cleans and returns to pool
- **WHEN** `recycle(container)` is called and `use_count < max_uses`
- **THEN** the container's `/tmp` directory is cleaned
- **AND** the container is marked as available in the pool

#### Scenario: Recycle destroys at max uses
- **WHEN** `recycle(container)` is called and `use_count >= max_uses`
- **THEN** the container is destroyed via `docker rm -f`
- **AND** the container is removed from the pool

#### Scenario: Recycle failure destroys container
- **WHEN** the cleanup command fails
- **THEN** the container is destroyed to prevent state contamination
- **AND** a warning is logged

### Requirement: TTL busy marker for crash recovery
The system SHALL track when each container was allocated. A background task SHALL periodically check for containers that have been in_use longer than `SANDBOX_POOL_BUSY_TTL` (default 1800 seconds / 30 minutes). Stale containers SHALL be force-released back to the pool.

#### Scenario: Crash recovery releases stale container
- **WHEN** a container has been in_use for longer than the busy TTL
- **THEN** the background task marks the container as available
- **AND** the container is cleaned before reuse

#### Scenario: Active container not affected
- **WHEN** a container has been in_use for less than the busy TTL
- **THEN** the background task does not modify its state

### Requirement: Idle trimming
The system SHALL monitor idle container count. When the number of idle containers exceeds `SANDBOX_POOL_SIZE` and an idle container has been idle for longer than `SANDBOX_POOL_IDLE_TIMEOUT` (default 300 seconds), the excess container SHALL be destroyed.

#### Scenario: Excess idle container trimmed after timeout
- **WHEN** pool has 5 idle containers, `pool_size=3`, and 2 containers have been idle for over 300 seconds
- **THEN** the 2 excess containers are destroyed
- **AND** the pool returns to `pool_size`

#### Scenario: Recently used container not trimmed
- **WHEN** a container has been idle for less than the idle timeout
- **THEN** it is not trimmed even if the pool exceeds `pool_size`

### Requirement: Exhaustion strategy
The system SHALL support two pool exhaustion strategies, configurable via `SANDBOX_POOL_EXHAUSTION_STRATEGY` (default `wait`):

- `wait`: Block until a container becomes available, up to `SANDBOX_POOL_ACQUIRE_TIMEOUT` seconds (default 30). Raise `PoolExhaustedError` on timeout.
- `temporary`: Create a temporary container outside the pool, use it, and destroy it after execution.

#### Scenario: WAIT strategy blocks then succeeds
- **WHEN** pool is exhausted and `wait` strategy is configured
- **THEN** `allocate()` blocks until a container is recycled or timeout
- **AND** if a container becomes available within timeout, it is returned

#### Scenario: WAIT strategy times out
- **WHEN** pool is exhausted and no container becomes available within `SANDBOX_POOL_ACQUIRE_TIMEOUT`
- **THEN** `allocate()` raises `PoolExhaustedError`

#### Scenario: TEMPORARY strategy creates ephemeral container
- **WHEN** pool is exhausted and `temporary` strategy is configured
- **THEN** a new container is created outside the pool, used for execution, and destroyed after
- **AND** the container is NOT returned to the pool

### Requirement: Pool disabled by default
The system SHALL default to `SANDBOX_POOL_ENABLED=false`. When disabled, the pool is not instantiated, `builtin.py::_execute_code()` and `port.tool_execute_sandbox()` use `SandboxExecutor` directly (existing per-execution container behavior).

#### Scenario: No overhead when disabled
- **WHEN** `SANDBOX_POOL_ENABLED=false`
- **THEN** no `SandboxPool` instance is created
- **AND** all sandbox executions use `SandboxExecutor.execute()` without `container_id`

#### Scenario: Pool enabled on startup
- **WHEN** `SANDBOX_POOL_ENABLED=true` and the application starts
- **THEN** the pool is initialized, prewarmed, and registered for use
- **AND** all sandbox executions route through the pool

### Requirement: Graceful shutdown flushes pool
The system SHALL destroy all pooled containers on application shutdown. Containers that are currently in_use SHALL be destroyed after their current execution completes or after the busy TTL expires, whichever comes first.

#### Scenario: Shutdown destroys all containers
- **WHEN** the application receives a shutdown signal
- **THEN** all idle containers are destroyed immediately
- **AND** in_use containers are destroyed after their execution completes or busy TTL expires

### Requirement: Configuration via environment variables
The system SHALL provide the following configuration options via environment variables, loaded through `core/config.py` Settings:

| Variable | Default | Description |
|----------|---------|-------------|
| `SANDBOX_POOL_ENABLED` | `false` | Enable/disable sandbox container pool |
| `SANDBOX_POOL_SIZE` | `3` | Number of containers to pre-warm |
| `SANDBOX_MAX_USES` | `50` | Maximum uses before container retirement |
| `SANDBOX_POOL_IDLE_TIMEOUT` | `300` | Seconds before trimming excess idle containers |
| `SANDBOX_POOL_ACQUIRE_TIMEOUT` | `30` | Seconds to wait when pool exhausted (WAIT strategy) |
| `SANDBOX_POOL_BUSY_TTL` | `1800` | Seconds before force-releasing stale in_use containers |
| `SANDBOX_POOL_EXHAUSTION_STRATEGY` | `wait` | Exhaustion strategy: `wait` or `temporary` |

#### Scenario: Custom pool size
- **WHEN** `SANDBOX_POOL_SIZE=10` is set
- **THEN** the pool pre-warms 10 containers on startup

#### Scenario: Invalid exhaustion strategy
- **WHEN** `SANDBOX_POOL_EXHAUSTION_STRATEGY=invalid` is set
- **THEN** the system falls back to `wait` strategy and logs a warning
