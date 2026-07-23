## 1. Config Settings

- [x] 1.1 Add `SANDBOX_MOUNT_MODE: str = "rw"` to `Settings` class in `src/hecate/core/config.py`
- [x] 1.2 Add `.env.example` entry for `SANDBOX_MOUNT_MODE` with comment

## 2. SandboxConfig Volume Support

- [x] 2.1 Add `volumes: dict[str, str] = field(default_factory=dict)` field to `SandboxConfig` dataclass in `src/hecate/services/sandbox/executor.py`
- [x] 2.2 In `SandboxExecutor._create_container()`, append `--volume {host}:{container}` args from `cfg.volumes` to the `docker_args` list, including the mount mode suffix from `settings.SANDBOX_MOUNT_MODE`
- [x] 2.3 Update `SandboxPool.__init__()` or `SandboxPool._acquire_new()` to propagate the executor's config volumes when creating containers (verify existing behavior already passes config through)

## 3. Environment Bridge

- [x] 3.1 Create `src/hecate/services/sandbox/environment_bridge.py` with `resolve_environment_volumes(env: AgentEnvironment | None) -> dict[str, str]` function
- [x] 3.2 Implement DockerEnvironment branch: import `DockerEnvironment`, check `isinstance`, return `{env._volume_name: "/mnt/env"}` (need to verify volume name attribute on DockerEnvironment)
- [x] 3.3 Implement LocalEnvironment branch: check `isinstance`, return `{str(env.root_path): "/mnt/env"}`
- [x] 3.4 Implement None branch: return `{}`
- [x] 3.5 Update `src/hecate/services/sandbox/__init__.py` to export `resolve_environment_volumes`

## 4. BuiltinTools Wiring

- [x] 4.1 Modify `BuiltinTools._execute_code()` in `src/hecate/services/tool/builtin.py` to accept an optional `environment: AgentEnvironment | None` parameter
- [x] 4.2 Call `resolve_environment_volumes(environment)` to get volume mounts
- [x] 4.3 Pass `SandboxConfig(volumes=volume_mounts)` to `SandboxExecutor()`
- [x] 4.4 Verify the call chain: find where `_execute_code` is called and ensure environment is passed through (check tool registration in WorkflowExecutionService or ToolWorker)

## 5. Tests

- [x] 5.1 Create `tests/test_services/test_sandbox/test_environment_bridge.py` with unit tests for `resolve_environment_volumes()`
- [x] 5.2 Test: DockerEnvironment resolves to named volume mapping `{volume_name: "/mnt/env"}`
- [x] 5.3 Test: LocalEnvironment resolves to host bind mount mapping `{root_path: "/mnt/env"}`
- [x] 5.4 Test: None environment resolves to empty dict `{}`
- [x] 5.5 Create `tests/test_services/test_sandbox/test_executor_volumes.py` with tests for SandboxExecutor volume mounting
- [x] 5.6 Test: SandboxConfig with empty volumes produces no `--volume` args in docker run command
- [x] 5.7 Test: SandboxConfig with volumes produces correct `--volume host:container:rw` args
- [x] 5.8 Test: SandboxConfig with `SANDBOX_MOUNT_MODE=ro` produces `:ro` suffix
- [x] 5.9 Test: execute_code with environment available passes volumes to SandboxExecutor (mock-based integration)
- [x] 5.10 Test: execute_code without environment passes empty volumes (backward compat)

## 6. Documentation

- [x] 6.1 Add docstrings to `resolve_environment_volumes()` and `SandboxConfig.volumes` field (English, per AGENTS.md)
- [x] 6.2 Update `src/hecate/services/sandbox/__init__.py` module docstring to mention environment mounting
- [x] 6.3 Add inline comment in `_create_container()` explaining volume mount args

## 7. Verification

- [x] 7.1 Run `ruff check src/hecate/ tests/` — expect 0 errors
- [x] 7.2 Run `ruff format --check src/ tests/` — expect all formatted
- [x] 7.3 Run `mypy src/` — expect 0 errors
- [x] 7.4 Run `python -m pytest tests/test_services/test_sandbox/ -v` — all pass
- [x] 7.5 Run `python -m pytest tests/test_services/test_context/ tests/test_engine/ tests/test_services/test_environment/ -q` — no regressions
