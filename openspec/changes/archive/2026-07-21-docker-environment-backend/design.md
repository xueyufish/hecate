## Context

Hecate's `AgentEnvironment` ABC (shipped in 1.3.15) provides a unified abstraction for an agent's persistent execution context — files, memory, sessions, skills. The only implementation, `LocalEnvironment`, stores data on the host filesystem at `{WORKSPACE_ROOT}/{agent_id}/`. This works for single-tenant development but provides no process isolation: all agents share the same OS, and a container escape or malicious tool could cross agent boundaries.

Industry research across 7 platforms (AgentScope, DeerFlow, Bedrock AgentCore, Google Gemini, Huawei AgentArts, Claude Code, Palantir/Salesforce) shows two isolation philosophies:

1. **Container/VM isolation** (AgentScope, DeerFlow, Bedrock, Google, Huawei) — each agent or session gets its own container or microVM with isolated filesystem and processes.
2. **Platform-level governance** (Palantir, Salesforce) — agents run on shared K8s with data-level security controls.

Hecate supports both self-hosted private deployment and SaaS, and agents can execute user-supplied code (Python, shell). This places Hecate firmly in the container/VM isolation camp. However, building a custom microVM platform (like Bedrock's Firecracker) is out of scope — the engineering cost is too high. The pragmatic path, validated by AgentScope and DeerFlow, is to self-build a Docker container backend using the `aiodocker` async library.

**Research basis**:
- **AgentScope**: `DockerBackend` uses `aiodocker` (`exec`, `get_archive`, `put_archive`) + `BackendBase` ABC (`exec_shell`, `read_file`, `write_file`). `SandboxedWorkspaceBase` template method for lifecycle. `WorkspaceManager` with TTL-based caching.
- **DeerFlow**: `SandboxProvider` abstraction with `LocalSandboxProvider` / `AioSandboxProvider` (Docker). Warm pool with LRU eviction and `keep_alive_seconds`. `Sandbox` interface: `execute_command`, `read_file`, `write_file`, `list_dir`. Virtual path mapping for thread isolation.
- **Docker Sandbox (sbx)**: Docker Inc.'s official microVM sandbox product. No comparable platform uses it — it's a developer-facing tool, not a platform backend. Regular Docker containers (namespace isolation) are the industry standard for agent platforms.

## Goals / Non-Goals

**Goals:**
- Add `exec_shell()` to `AgentEnvironment` ABC for shell command execution inside the environment
- Implement `DockerEnvironment` using `aiodocker` with persistent volumes per agent
- Refactor `EnvironmentManager` to support backend selection (`local` / `docker`) via config
- Support optional gVisor runtime (`runsc`) for stronger isolation
- Warm pool for container reuse to reduce cold-start latency
- Zero breaking changes to existing `LocalEnvironment` callers

**Non-Goals:**
- E2B cloud sandbox backend (conflicts with private deployment positioning — data would leave the machine room)
- AerolVM / ForgeVM integration (all mainstream platforms self-build; external dependency adds operational burden)
- MCP gateway inside container (deferred to follow-up; current MCP servers run in Hecate main process)
- Firecracker / Kata Containers backends (tracked as 6.40 and 6.32a — future features that build on this foundation)
- K8s-native backend (tracked as future deployment feature)
- Per-session isolation (environment is per-agent, matching existing 1.3.15 design; per-session state is 1.3.16's domain)

## Decisions

### Decision 1: Self-build with aiodocker, not external service

**Choice**: Use `aiodocker` library to manage Docker containers directly.

**Rationale**: Every mainstream platform self-builds its sandbox infrastructure. AgentScope and DeerFlow both use Docker daemon APIs directly. External services (E2B, AerolVM, ForgeVM) are either cloud-only (data sovereignty conflict) or add an extra service to deploy (operational burden). Self-building with `aiodocker` gives full control, no external dependencies, and is proven by AgentScope.

**Alternatives considered**:
- `docker` CLI via subprocess (existing `SandboxExecutor` 9.4c approach): rejected — subprocess overhead per operation, harder to manage streaming output, no async support.
- Docker SDK (`docker-py`): rejected — synchronous only, would need thread pool wrapper.
- AerolVM/ForgeVM: rejected — all mainstream platforms self-build; adds external service dependency.

### Decision 2: Long-running container with named volume, not ephemeral

**Choice**: Each agent gets a long-running Docker container with a named volume (`agent-{agent_id}`) mounted at `/env`.

**Rationale**:
- Matches `AgentEnvironment`'s persistence semantics (files survive across sessions)
- Bedrock uses this model (session-scoped microVM with persistent storage)
- DeerFlow uses `keep_alive_seconds: 3600` for container reuse
- AgentScope's `WorkspaceManager` keeps containers alive with TTL eviction
- Alternative (ephemeral per-request) would require volume snapshot/restore per operation — too slow

**Container lifecycle**:
```
get_or_create(agent_id)
  → check warm pool for idle container
  → found: reuse (reset TTL timer)
  → not found: docker.containers.create() + start()

close(agent_id)
  → stop container, move to warm pool (not destroy)
  → warm pool full: destroy container (volume persists)

close_all()
  → destroy all containers (volumes persist for future reuse)
```

### Decision 3: File I/O via Docker exec and tar archive

**Choice**: File operations use Docker's container API:
- `read_file`: `container.get_archive(path)` → extract from tar → return bytes
- `write_file`: create tar in memory → `container.put_archive(parent_dir, tar_bytes)`
- `exec_shell`: `container.exec(cmd)` → stream stdout/stderr → return `ExecResult`
- `list_files`, `delete_file`, `exists`: implemented via `exec_shell` (e.g., `ls -la`, `rm`, `test -e`)

**Rationale**: This is exactly AgentScope's `DockerBackend` pattern. The three primitives (`exec_shell`, `read_file`, `write_file`) are sufficient — all other file operations compose from `exec_shell`. This keeps the implementation small and tested.

### Decision 4: Add exec_shell to AgentEnvironment ABC

**Choice**: Extend the existing ABC with `exec_shell(command, *, cwd, timeout) -> ExecResult`.

**Rationale**:
- Container backends fundamentally need shell execution (install packages, run setup scripts, operate files)
- AgentScope's `BackendBase` proves 3 primitives (`exec_shell` + `read_file` + `write_file`) are the minimal clean interface
- `LocalEnvironment` can trivially implement it via `asyncio.create_subprocess_exec`
- Future backends (gVisor, Kata, Firecracker) all need it too

**Alternatives**: Keep ABC file-only and put `exec_shell` on a separate `SandboxBackend` ABC. Rejected — two ABCs for the same concept is confusing; DeerFlow and AgentScope both unify them.

### Decision 5: Backend selection via config, not DI parameter

**Choice**: `EnvironmentManager` reads `settings.AGENT_ENV_BACKEND` (`"local"` or `"docker"`) to select the backend factory.

**Rationale**: Matches Hecate's existing config-driven pattern (`settings.AGENT_ENV_TTL`, `settings.WORKSPACE_ROOT`). Keeps `EnvironmentManager` constructor signature stable. Users switch backends by changing `.env`, not code.

### Decision 6: Warm pool inspired by existing SandboxPool (9.4d)

**Choice**: Implement a lightweight warm pool inside `EnvironmentManager` — closed containers go to an idle list with configurable max size and idle timeout. Reuse on next `get_or_create` for the same agent.

**Rationale**: Avoids ~200ms container cold-start on every session. Pattern proven by DeerFlow (`keep_alive_seconds`) and AgentScope (`WorkspaceManager` TTL). Existing `SandboxPool` (9.4d) validates the pool concept but is designed for ephemeral tool execution, not persistent environments — different lifecycle, so a separate pool is cleaner.

## Risks / Trade-offs

- **[Docker daemon dependency]** — `docker` backend requires Docker daemon on the host. Mitigation: `local` backend remains the default; Docker is opt-in via `AGENT_ENV_BACKEND=docker`. CI/test suite uses `local` backend.

- **[Single-instance only]** — Like `LocalEnvironment`, the Docker warm pool is in-process. Multi-instance Hecate deployments would each manage their own containers. Mitigation: containers are stateless beyond their volumes; any instance can pick up a volume. Full multi-instance coordination is a future K8s feature.

- **[Container cold start ~200ms]** — First creation of a container takes ~200ms. Mitigation: warm pool keeps idle containers alive for reuse. Subsequent `get_or_create` calls are instant.

- **[aiodocker version compatibility]** — `aiodocker` API may change between versions. Mitigation: pin version in `pyproject.toml`; wrap Docker API calls in `DockerEnvironment` methods so adapter changes are localized.

- **[gVisor availability]** — `runsc` runtime is not available on all hosts (requires Linux 4.x+, specific kernel configs). Mitigation: `DOCKER_RUNTIME` defaults to `runc`; gVisor is explicitly opt-in. Document prerequisite in config comment.

- **[ABC breakage risk]** — Adding `exec_shell` to `AgentEnvironment` ABC means all implementations must provide it. Mitigation: only two implementations exist (`LocalEnvironment` + new `DockerEnvironment`); both ship in this change. Third-party implementations (if any) would need updating, but the ABC is internal to Hecate.
