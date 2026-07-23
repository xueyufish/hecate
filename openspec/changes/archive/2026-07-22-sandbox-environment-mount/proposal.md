## Why

The SandboxExecutor (9.4c) and AgentEnvironment (1.3.15) are two Docker-based subsystems that currently operate in complete isolation. The SandboxExecutor spins up ephemeral containers with no persistent storage, while the DockerEnvironment maintains long-lived containers with named volumes. This means an agent cannot write code to its environment and then execute that code in an isolated sandbox — the two systems are disconnected.

This gap blocks the core "coding agent" workflow: write code to environment → execute in sandbox → read results. Amazon Bedrock AgentCore solves this with `/mnt/workspace` shared volumes; deer-flow uses `/mnt/user-data/workspace/` bind mounts; private-gpt uses `SessionMountDef` with shared volumes. Hecate needs the same capability.

## What Changes

- **MODIFIED**: `SandboxConfig` gains a `volumes` field for specifying Docker volume/bind mounts to attach to sandbox containers.
- **MODIFIED**: `SandboxExecutor._create_container()` appends `--volume` arguments from `SandboxConfig.volumes` to the `docker run` command.
- **MODIFIED**: `SandboxPool` propagates `volumes` from its executor's config when creating containers.
- **NEW**: `SandboxEnvironmentConfig` builder in `services/sandbox/environment_bridge.py` that constructs the volume mount config for both DockerEnvironment (shared volume) and LocalEnvironment (bind mount) scenarios.
- **MODIFIED**: `ToolWorker` / `BuiltinTools._execute_code()` passes the agent's environment mount config to SandboxExecutor when an AgentEnvironment is available.
- **NEW**: Config setting `SANDBOX_MOUNT_MODE` (default `"rw"`) — controls read/write permissions for the environment mount inside sandbox.

## Capabilities

### New Capabilities
- `sandbox-environment-mount`: The capability for mounting an AgentEnvironment into a SandboxExecutor container, covering volume resolution (Docker volume vs bind mount), mount path (`/mnt/env`), permissions, and lifecycle coordination.

### Modified Capabilities
- `sandbox-executor`: SandboxConfig gains a `volumes` dict; `_create_container()` mounts them. No behavioral change to existing sandbox semantics — purely additive.

## Impact

- **Code**:
  - `src/hecate/services/sandbox/executor.py` (MODIFIED) — `SandboxConfig` + `_create_container()`
  - `src/hecate/services/sandbox/pool.py` (MODIFIED) — propagate volumes
  - `src/hecate/services/sandbox/environment_bridge.py` (NEW) — volume mount builder
  - `src/hecate/services/tool/builtin.py` (MODIFIED) — pass env mount to sandbox
  - `src/hecate/core/config.py` (MODIFIED) — new setting
- **APIs**: No external API changes. Internal `SandboxConfig` gains optional `volumes` field.
- **Dependencies**: No new external dependencies. Uses existing Docker `--volume` flag.
- **Storage**: Sandbox containers gain read-write access to the agent's environment volume at `/mnt/env`.
