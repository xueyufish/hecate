## 1. SandboxExecutor: docker exec support

- [x] 1.1 Add optional `container_id: str | None = None` keyword parameter to `SandboxExecutor.execute()`
- [x] 1.2 Implement `_exec_in_container(container_id, tool_name, args, config)` private method using `docker exec` with timeout handling
- [x] 1.3 Route in `execute()`: if `container_id` provided → `_exec_in_container()`, else → existing `_create_and_run()` path
- [x] 1.4 Ensure `docker exec` timeout terminates the exec process but does NOT destroy the container
- [x] 1.5 Update existing SandboxExecutor tests to cover both paths (with and without container_id)

## 2. SandboxPool: fix execute + add production features

- [x] 2.1 Fix `SandboxPool.execute()` to pass `container_id=container.container_id` to `executor.execute()`
- [x] 2.2 Add health check on acquire: `docker exec <id> true` before returning container to caller; discard dead containers
- [x] 2.3 Add `allocated_at` timestamp to `PooledContainer` for TTL busy marker tracking
- [x] 2.4 Add `_reap_stale_containers()` background task: force-release containers in_use longer than `SANDBOX_POOL_BUSY_TTL`
- [x] 2.5 Add idle trimming: track `last_used_at`, destroy excess idle containers older than `SANDBOX_POOL_IDLE_TIMEOUT`
- [x] 2.6 Add exhaustion strategy support: `wait` (block with timeout) and `temporary` (create-and-destroy outside pool)
- [x] 2.7 Add `PoolExhaustedError` exception for WAIT timeout
- [x] 2.8 Update `_create_fresh_container()` to use `sleep infinity` entrypoint (already done, verify)

## 3. Configuration

- [x] 3.1 Add settings to `core/config.py`: `SANDBOX_POOL_ENABLED` (bool, default False)
- [x] 3.2 Add `SANDBOX_POOL_SIZE` (int, default 3), `SANDBOX_MAX_USES` (int, default 50)
- [x] 3.3 Add `SANDBOX_POOL_IDLE_TIMEOUT` (int, default 300), `SANDBOX_POOL_ACQUIRE_TIMEOUT` (int, default 30)
- [x] 3.4 Add `SANDBOX_POOL_BUSY_TTL` (int, default 1800)
- [x] 3.5 Add `SANDBOX_POOL_EXHAUSTION_STRATEGY` (str, default "wait") with validation (fallback to "wait" on invalid value)
- [x] 3.6 Update `.env.example` with all new sandbox pool variables and comments

## 4. Wiring: execution path integration

- [x] 4.1 Create `get_sandbox_pool()` singleton accessor in `services/sandbox/__init__.py` — returns pool instance or None if disabled
- [x] 4.2 Wire `builtin.py::_execute_code()`: use pool when enabled, fallback to direct SandboxExecutor when disabled
- [x] 4.3 Wire `port.tool_execute_sandbox()` service adapter: use pool when enabled, fallback to direct SandboxExecutor when disabled
- [x] 4.4 Add pool lifecycle to `main.py`: prewarm on startup, shutdown on cleanup (lifespan context)

## 5. Tests: fix broken tests + add new coverage

- [x] 5.1 Fix `test_execute_delegates_to_executor` — update to verify `container_id` is passed to executor
- [x] 5.2 Add test: health check on acquire detects dead container and replaces it
- [x] 5.3 Add test: health check on acquire with healthy container passes through
- [x] 5.4 Add test: TTL busy marker reaps stale in_use container
- [x] 5.5 Add test: idle trimming destroys excess idle containers after timeout
- [x] 5.6 Add test: WAIT exhaustion strategy blocks then succeeds within timeout
- [x] 5.7 Add test: WAIT exhaustion strategy raises PoolExhaustedError on timeout
- [x] 5.8 Add test: TEMPORARY exhaustion strategy creates and destroys ephemeral container
- [x] 5.9 Add test: recycle destroys container at max_uses
- [x] 5.10 Add test: recycle failure destroys container (prevents state contamination)
- [x] 5.11 Add test: SandboxExecutor.execute() with container_id uses docker exec path
- [x] 5.12 Add test: SandboxExecutor.execute() without container_id uses docker run path (backward compat)
- [x] 5.13 Add test: pool disabled by default — no SandboxPool instance created
- [x] 5.14 Add test: graceful shutdown destroys all containers

## 6. Verification

- [x] 6.1 Run `ruff check src/hecate/ tests/` — zero errors
- [x] 6.2 Run `ruff format --check src/ tests/` — zero errors
- [x] 6.3 Run `mypy src/` — zero errors
- [x] 6.4 Run `python -m pytest tests/ -q` — all tests pass, no regressions
