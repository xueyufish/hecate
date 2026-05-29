## 1. Sandbox Executor

- [x] 1.1 Create `services/sandbox/executor.py` with SandboxExecutor class
- [x] 1.2 Implement execute(tool_name, args, config) — run tool in Docker container
- [x] 1.3 Implement resource limits — CPU, memory, network, filesystem isolation
- [x] 1.4 Implement timeout handling — destroy container on timeout

## 2. Sandbox Pool

- [x] 2.1 Create `services/sandbox/pool.py` with SandboxPool class
- [x] 2.2 Implement pre-warming — create N containers on startup
- [x] 2.3 Implement allocation — get sandbox from pool or create new
- [x] 2.4 Implement recycling — clean and return sandbox to pool
- [x] 2.5 Implement max-uses policy — destroy after N uses

## 3. Integration

- [x] 3.1 Extend EnginePort — add tool_execute_sandbox method
- [x] 3.2 Add sandbox configuration to Tool model

## 4. Testing

- [x] 4.1 Unit tests for SandboxExecutor — execute, timeout, resource limits
- [x] 4.2 Unit tests for SandboxPool — pre-warming, allocation, recycling
- [x] 4.3 Integration test with Docker
