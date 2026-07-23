# sandbox-environment-mount Specification

## Purpose
TBD - created by archiving change sandbox-environment-mount. Update Purpose after archive.
## Requirements
### Requirement: SandboxConfig supports volume mounts

The `SandboxConfig` dataclass SHALL include a `volumes` field of type `dict[str, str]` where keys are host paths or Docker volume names and values are container mount paths. The field SHALL default to an empty dict, preserving backward compatibility.

#### Scenario: SandboxConfig with no volumes

- **WHEN** `SandboxConfig()` is constructed without specifying `volumes`
- **THEN** `volumes` SHALL be an empty dict `{}`
- **AND** the sandbox container SHALL be created with no `--volume` arguments

#### Scenario: SandboxConfig with environment volume

- **WHEN** `SandboxConfig(volumes={"agent-abc123": "/mnt/env"})` is constructed
- **THEN** `volumes` SHALL contain `{"agent-abc123": "/mnt/env"}`
- **AND** the sandbox container SHALL be created with `--volume agent-abc123:/mnt/env`

### Requirement: SandboxExecutor mounts volumes when configured

`SandboxExecutor._create_container()` SHALL append `--volume {host}:{container}` arguments to the `docker run` command for each entry in `SandboxConfig.volumes`.

#### Scenario: Single volume mount

- **WHEN** `SandboxConfig(volumes={"/workspace/agent-1": "/mnt/env"})` is passed to `SandboxExecutor`
- **AND** `execute("run_code", {"code": "print('hi')"})` is called
- **THEN** the `docker run` command SHALL include `--volume /workspace/agent-1:/mnt/env`
- **AND** the container SHALL have access to the agent's files at `/mnt/env`

#### Scenario: Multiple volume mounts

- **WHEN** `SandboxConfig(volumes={"/data": "/mnt/data", "/config": "/mnt/config"})` is passed
- **THEN** the `docker run` command SHALL include both `--volume /data:/mnt/data` and `--volume /config:/mnt/config`

#### Scenario: Empty volumes produces no mount args

- **WHEN** `SandboxConfig(volumes={})` is passed
- **THEN** the `docker run` command SHALL NOT contain any `--volume` arguments

### Requirement: Environment bridge resolves volume mounts from AgentEnvironment

A `resolve_environment_volumes()` function SHALL accept an `AgentEnvironment` (or None) and return a `dict[str, str]` mapping for SandboxConfig volumes. The function SHALL handle DockerEnvironment and LocalEnvironment differently.

#### Scenario: DockerEnvironment resolves to named volume

- **WHEN** `resolve_environment_volumes()` is called with a `DockerEnvironment(agent_id="agent-abc")`
- **THEN** the return value SHALL be `{"agent-abc": "/mnt/env"}`
- **AND` the key SHALL be the Docker volume name used by the environment container

#### Scenario: LocalEnvironment resolves to host bind mount

- **WHEN** `resolve_environment_volumes()` is called with a `LocalEnvironment(root="/workspace/agent-1")`
- **THEN** the return value SHALL be `{"/workspace/agent-1": "/mnt/env"}`
- **AND` the key SHALL be the absolute host path

#### Scenario: No environment returns empty mapping

- **WHEN** `resolve_environment_volumes(None)` is called
- **THEN** the return value SHALL be `{}`

### Requirement: Sandbox mount mode is configurable

A `SANDBOX_MOUNT_MODE` config setting SHALL control the Docker mount permission suffix. Valid values are `"rw"` (read-write, default) and `"ro"` (read-only).

#### Scenario: Default mount mode is rw

- **WHEN** `SANDBOX_MOUNT_MODE` is not set
- **THEN** volume mounts SHALL include the `:rw` suffix (e.g., `--volume agent-x:/mnt/env:rw`)

#### Scenario: Mount mode ro

- **WHEN** `SANDBOX_MOUNT_MODE=ro` is set
- **THEN** volume mounts SHALL include the `:ro` suffix (e.g., `--volume agent-x:/mnt/env:ro`)

### Requirement: SandboxPool propagates volume config

`SandboxPool` SHALL propagate the executor's `SandboxConfig.volumes` when creating new containers via the pool.

#### Scenario: Pool uses executor config volumes

- **WHEN** `SandboxPool(executor=SandboxExecutor(config=SandboxConfig(volumes={"/data": "/mnt/env"})))` is constructed
- **AND` the pool creates a new container
- **THEN** the container SHALL have `/data` mounted at `/mnt/env`

### Requirement: BuiltinTools passes environment mount to SandboxExecutor

`BuiltinTools._execute_code()` SHALL resolve the agent's environment volume mounts and pass them to SandboxExecutor when an AgentEnvironment is available.

#### Scenario: execute_code with environment available

- **WHEN** `_execute_code()` is called and an `AgentEnvironment` is available
- **THEN** it SHALL call `resolve_environment_volumes(env)` to get volume mounts
- **AND` pass `SandboxConfig(volumes=volume_mounts)` to SandboxExecutor
- **AND` the sandbox container SHALL have the environment mounted at `/mnt/env`

#### Scenario: execute_code without environment

- **WHEN** `_execute_code()` is called and no `AgentEnvironment` is available
- **THEN` it SHALL pass `SandboxConfig(volumes={})` to SandboxExecutor
- **AND` the sandbox container SHALL have no volume mounts (backward compatible)

#### Scenario: Agent reads sandbox output via environment

- **WHEN** sandbox writes `output.txt` to `/mnt/env/files/output.txt`
- **AND` the sandbox container finishes
- **THEN` the agent SHALL be able to read the file via `read_file("files/output.txt")` on its environment

