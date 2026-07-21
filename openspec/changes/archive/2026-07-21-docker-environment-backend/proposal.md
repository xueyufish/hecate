## Why

Hecate's `AgentEnvironment` ABC (1.3.15) only ships with `LocalEnvironment` — a filesystem-backed implementation that provides no process-level isolation between agents. In multi-tenant deployments where agents run user-supplied code, a container escape or path traversal could expose one tenant's files to another. Every comparable platform (AgentScope, DeerFlow, Bedrock AgentCore, Google Gemini) provides containerized or microVM-isolated execution environments. This change adds a `DockerEnvironment` backend so agents can run inside Docker containers with isolated filesystems, processes, and optional gVisor hardening — all self-hosted with no external cloud dependency.

## What Changes

- **Add `exec_shell()` to `AgentEnvironment` ABC** — the existing ABC has only file I/O methods (read/write/list/delete/exists/ensure_dirs). Container backends need shell execution to operate (file ops via `docker exec`, package installation, tool setup). Adding `exec_shell(command) -> ExecResult` brings Hecate to parity with AgentScope's `BackendBase` and DeerFlow's `Sandbox` interface.
- **Add `ExecResult` dataclass** — structured return type for `exec_shell`: `exit_code`, `stdout`, `stderr`.
- **Implement `DockerEnvironment`** — new `AgentEnvironment` implementation backed by Docker containers via `aiodocker`. Each agent gets its own long-running container with a named volume for persistent filesystem (sessions/, files/, memory/, skills/). File operations use Docker's `exec` / `get_archive` / `put_archive` APIs. Container lifecycle managed with warm pool reuse.
- **Implement `LocalEnvironment.exec_shell()`** — the existing `LocalEnvironment` gains an `exec_shell` implementation using `asyncio.create_subprocess_exec` on the host.
- **Refactor `EnvironmentManager` for backend selection** — currently hardcoded to `LocalEnvironment`. Add config-driven backend selection (`AGENT_ENV_BACKEND=local|docker`) and a warm pool for container reuse (informed by existing `SandboxPool` 9.4d patterns).
- **Add `aiodocker` dependency** — async Docker API client for Python. Added to `[tools]` optional dependency group in `pyproject.toml`.
- **Add Docker configuration** — new settings: `AGENT_ENV_BACKEND`, `DOCKER_AGENT_IMAGE`, `DOCKER_RUNTIME` (runc/runsc), `DOCKER_NETWORK_MODE`.
- **Optional gVisor support** — when `DOCKER_RUNTIME=runsc`, containers use gVisor user-space kernel for stronger isolation. Requires `runsc` installed on host.

## Capabilities

### New Capabilities

_(none — all capabilities reference existing specs)_

### Modified Capabilities

- `agent-environment`: Add `exec_shell` abstract method to `AgentEnvironment` ABC. Add `DockerEnvironment` as a second backend implementation alongside `LocalEnvironment`. Add `ExecResult` return type. Refactor `EnvironmentManager` to support backend selection and warm pool.

## Impact

- **Modified files**:
  - `src/hecate/services/environment/environment.py` — add `exec_shell` to ABC + `LocalEnvironment`; add `ExecResult` dataclass; add `DockerEnvironment` class
  - `src/hecate/services/environment/manager.py` — refactor `get_or_create()` for backend selection; add warm pool logic
  - `src/hecate/core/config.py` — new settings: `AGENT_ENV_BACKEND`, `DOCKER_AGENT_IMAGE`, `DOCKER_RUNTIME`, `DOCKER_NETWORK_MODE`
  - `pyproject.toml` — add `aiodocker` to `[tools]` group
- **New files**:
  - `src/hecate/services/environment/docker_environment.py` — `DockerEnvironment` implementation (or co-located in `environment.py` if size permits)
  - `tests/test_services/test_environment/test_docker_environment.py` — unit tests for `DockerEnvironment`
  - `tests/test_services/test_environment/test_exec_shell.py` — tests for `exec_shell` across backends
- **No breaking changes**: `AGENT_ENV_BACKEND` defaults to `"local"`. Existing `LocalEnvironment` callers see no behavior change. `exec_shell` is additive to the ABC.
- **New dependencies**: `aiodocker` (async Docker client). Only imported when `AGENT_ENV_BACKEND=docker`.
- **Infrastructure requirement**: Docker daemon must be available when using `docker` backend. gVisor (`runsc`) optional for stronger isolation.
