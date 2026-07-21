## 1. AgentEnvironment ABC: Add exec_shell

- [x] 1.1 Add `ExecResult` dataclass to `src/hecate/services/environment/environment.py` with fields `exit_code: int`, `stdout: bytes`, `stderr: bytes`
- [x] 1.2 Add `exec_shell(self, command: list[str], *, cwd: str | None = None, timeout: float | None = None) -> ExecResult` as abstract method to `AgentEnvironment` ABC
- [x] 1.3 Implement `exec_shell` on `LocalEnvironment` using `asyncio.create_subprocess_exec` — run command on host, capture stdout/stderr separately, handle timeout with `asyncio.wait_for`

## 2. DockerEnvironment Implementation

- [x] 2.1 Add `aiodocker` to `[tools]` optional dependency group in `pyproject.toml`
- [x] 2.2 Create `DockerEnvironment` class in `src/hecate/services/environment/environment.py` (or new `docker_environment.py` if file gets too large) implementing all `AgentEnvironment` abstract methods
- [x] 2.3 Implement container lifecycle: `__init__` stores agent_id + config; `_ensure_container()` lazily creates or reuses container via `aiodocker.Docker()` client
- [x] 2.4 Implement `read_file(path)` using `container.get_archive(path)` → tar extraction → return bytes (reference: AgentScope `DockerBackend.read_file`)
- [x] 2.5 Implement `write_file(path, content)` using in-memory tar creation → `container.put_archive(parent_dir, tar_bytes)` (reference: AgentScope `DockerBackend.write_file`)
- [x] 2.6 Implement `exec_shell(command)` using `container.exec(cmd)` → stream stdout/stderr → return `ExecResult` (reference: AgentScope `DockerBackend.exec_shell`)
- [x] 2.7 Implement `list_files(path)`, `delete_file(path)`, `exists(path)` via `exec_shell` composition (e.g., `ls -la`, `rm`, `test -e`) — parse output into `FileInfo` where needed
- [x] 2.8 Implement `ensure_dirs()` — `exec_shell(["mkdir", "-p", ...])` for sessions/, files/, memory/, skills/
- [x] 2.9 Implement `root_path` and `environment_id` properties appropriate for container context (e.g., `environment_id` = agent_id, `root_path` = `/env`)
- [x] 2.10 Support configurable runtime: pass `runtime` (runc/runsc) to container creation when `DOCKER_RUNTIME` is set

## 3. EnvironmentManager Refactor

- [x] 3.1 Add `AGENT_ENV_BACKEND` setting to `src/hecate/core/config.py` (type: `str`, default: `"local"`, choices: `["local", "docker"]`)
- [x] 3.2 Add Docker-specific settings to config: `DOCKER_AGENT_IMAGE` (default: `"python:3.12-slim"`), `DOCKER_RUNTIME` (default: `"runc"`), `DOCKER_NETWORK_MODE` (default: `"none"`), `DOCKER_WARM_POOL_SIZE` (default: 10), `DOCKER_WARM_POOL_IDLE_TIMEOUT` (default: 3600)
- [x] 3.3 Validate `AGENT_ENV_BACKEND` value at `EnvironmentManager.__init__` — raise `ValueError` for unrecognized values
- [x] 3.4 Refactor `EnvironmentManager.get_or_create()` to select backend based on `AGENT_ENV_BACKEND`: `"local"` → `LocalEnvironment`, `"docker"` → `DockerEnvironment`
- [x] 3.5 Implement warm pool for Docker backend: `close(agent_id)` moves container to idle list instead of destroying; `get_or_create(agent_id)` checks warm pool first
- [x] 3.6 Implement warm pool eviction: when pool is full, destroy oldest idle container; sweep idle containers past timeout on each `get_or_create`
- [x] 3.7 Ensure `close_all()` destroys all containers for docker backend (volumes persist)

## 4. Tests

- [x] 4.1 Test `LocalEnvironment.exec_shell`: basic command execution, working directory, timeout, stderr capture
- [x] 4.2 Test `ExecResult` dataclass: field types, default values
- [x] 4.3 Test `DockerEnvironment` container creation: image pull, volume mount, subdirectory creation — **requires Docker daemon, skip if unavailable** (`pytest.mark.skipif`)
- [x] 4.4 Test `DockerEnvironment` file operations: write → read roundtrip, list_files, delete_file, exists — **requires Docker daemon**
- [x] 4.5 Test `DockerEnvironment.exec_shell`: command runs inside container, returns correct exit_code/stdout/stderr — **requires Docker daemon**
- [x] 4.6 Test `EnvironmentManager` backend selection: `"local"` creates `LocalEnvironment`, `"docker"` creates `DockerEnvironment`, invalid value raises `ValueError`
- [x] 4.7 Test warm pool: container moves to pool on close, reuses on re-access, evicts when full, sweeps on timeout
- [x] 4.8 Test that default config (`AGENT_ENV_BACKEND` unset) preserves existing `LocalEnvironment` behavior (no regressions)

## 5. Documentation

- [x] 5.1 Update `src/hecate/services/environment/__init__.py` exports to include `DockerEnvironment` and `ExecResult`
- [x] 5.2 Add docstrings to all new public classes and methods (English, per coding rules)
- [x] 5.3 Add config documentation comments in `config.py` for new Docker-related settings

## 6. Verification

- [x] 6.1 Run `ruff check src/hecate/ tests/` — expect 0 errors
- [x] 6.2 Run `ruff format --check src/ tests/` — expect all formatted
- [x] 6.3 Run `mypy src/` — expect 0 errors
- [x] 6.4 Run `python -m pytest tests/test_services/test_environment/ -v` — all pass (Docker tests skip if no daemon)
- [x] 6.5 Run `python -m pytest tests/ -q` — no regressions
