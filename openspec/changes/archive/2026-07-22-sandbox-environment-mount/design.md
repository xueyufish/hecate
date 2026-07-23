## Context

Hecate has two Docker-based subsystems that currently operate in isolation:

**SandboxExecutor** (`services/sandbox/executor.py`): Creates ephemeral Docker containers for code execution via `execute_code` tool. Containers are created with `docker run --rm` and destroyed after each execution. No persistent storage, no volume mounts, no awareness of the agent's environment.

**AgentEnvironment** (`services/environment/`): Long-lived containers (DockerEnvironment) or host directories (LocalEnvironment) with per-agent persistent storage at `/env` containing `sessions/`, `files/`, `memory/`, `skills/`. Agents write files via `write_file()` and execute commands via `exec_shell()`.

The gap: an agent cannot write `solution.py` to its environment and then execute `python solution.py` in an isolated sandbox. The two systems have no connection.

**Industry patterns** (from research):
- Amazon Bedrock AgentCore: `/mnt/workspace` shared volume, 14-day TTL
- deer-flow: `/mnt/user-data/workspace/` bind mount per thread
- private-gpt: `SessionMountDef` with canonical paths (`/home/agent/workspace/`)
- OpenHands: `/workspace` bind mount per session
- Sage (bwrap): `--ro-bind sandbox_agent_workspace`

All use bind mount or shared volume to connect the agent's persistent files to the sandbox's execution context.

**Constraints:**
- SandboxExecutor is in `services/`, AgentEnvironment is in `services/` — same layer, no layering violation.
- Engine layer (`engine/workers/tool_worker.py`) calls the tool service which creates SandboxExecutor — the bridge must be wired at the service layer, not engine layer.
- DockerEnvironment uses a named Docker volume (`agent-{agent_id}`); LocalEnvironment uses a host directory (`{WORKSPACE_ROOT}/{agent_id}/`). The bridge must handle both.
- The SandboxExecutor uses `docker run` CLI, not `aiodocker`. Volume mounts must be expressed as `--volume` CLI args.

## Goals / Non-Goals

**Goals:**
- Allow SandboxExecutor to mount the agent's environment volume/directory at `/mnt/env` inside sandbox containers
- Support both DockerEnvironment (shared Docker volume) and LocalEnvironment (host bind mount)
- Mount as `rw` by default so sandbox can write output files back to the environment
- Make volume mount opt-in per execution (not all sandbox calls need environment access)
- Keep SandboxConfig backward compatible — existing callers without volumes work unchanged

**Non-Goals:**
- Mounting environment into non-Docker sandbox backends (bwrap, macOS sandbox-exec) — future work
- Read-only mounts — defaulting to `rw` for code execution use cases; `ro` support deferred
- Sandbox warm pool integration with environment — the pool manages containers, not volumes
- Changing the SandboxExecutor's container lifecycle (still ephemeral `--rm`) — only adding volume mounts

## Decisions

### Decision 1: Add `volumes` dict to SandboxConfig

**Choice:** Add `volumes: dict[str, str]` to `SandboxConfig` where keys are host paths or volume names and values are container mount paths.

**Rationale:** This is the minimal, Docker-native abstraction. `--volume host_path:container_path` is the standard Docker mount syntax. A dict naturally expresses the mapping.

**Alternatives considered:**
- *Separate `environment_volume` field*: Less flexible; can't mount multiple volumes. Rejected.
- *String list `["agent-x:/mnt/env"]`*: Harder to programmatically compose. Rejected.
- *Dedicated `MountSpec` dataclass*: Over-engineering for a dict. Rejected for now.

### Decision 2: Bridge via `environment_bridge.py` module

**Choice:** Create `services/sandbox/environment_bridge.py` with a `resolve_environment_volumes()` function that takes an `AgentEnvironment` and returns a `dict[str, str]` volume mapping.

**Rationale:** The logic for resolving an AgentEnvironment to a Docker volume mount differs between DockerEnvironment (volume name) and LocalEnvironment (host path). A dedicated module encapsulates this without polluting SandboxExecutor or AgentEnvironment.

**Resolution logic:**
- DockerEnvironment → `{"agent-{agent_id}": "/mnt/env"}` (Docker named volume)
- LocalEnvironment → `{"{root_path}": "/mnt/env"}` (host bind mount)
- None → `{}` (no mount)

### Decision 3: Mount path is `/mnt/env`

**Choice:** All environment mounts go to `/mnt/env` inside the sandbox container.

**Rationale:**
- Consistent with Bedrock's `/mnt/workspace` convention
- Doesn't conflict with the sandbox image's working directory
- Agents/tools can reference `/mnt/env/files/solution.py` uniformly regardless of backend

### Decision 4: Mount mode defaults to `rw`

**Choice:** `SANDBOX_MOUNT_MODE` config setting defaults to `"rw"`. Passed as `:rw` suffix to `--volume` args.

**Rationale:** Code execution needs to write output files. Bedrock, private-gpt, and deer-flow all use read-write mounts. Read-only is the exception, not the default.

### Decision 5: Bridge is wired at tool execution layer

**Choice:** `BuiltinTools._execute_code()` resolves the environment mount and passes it to SandboxExecutor.

**Rationale:** This is where `SandboxExecutor` is constructed today. The tool layer already has access to the environment (via WorkflowExecutionService). No engine-layer changes needed.

**Flow:**
```
ToolWorker.execute()
  → BuiltinTools._execute_code(args, environment=env)
    → volumes = resolve_environment_volumes(env)
    → SandboxExecutor(config=SandboxConfig(volumes=volumes))
      → _create_container() adds --volume args
```

### Decision 6: No engine-layer changes

**Choice:** PregelRuntime, LLMWorker, and ToolWorker remain unchanged. The bridge is entirely in the services layer.

**Rationale:** Engine has zero external deps (AGENTS.md). SandboxExecutor is a service. The bridge must live in services/ and be wired by the services layer that constructs SandboxExecutor.

## Risks / Trade-offs

- **[Docker volume name collision]** If two agents share a volume name prefix, cross-contamination is possible. → Mitigation: volume names are `agent-{agent_id}` with UUID — collision probability near zero.
- **[rw mount allows sandbox to corrupt environment]** A malicious or buggy sandbox execution could modify agent files. → Mitigation: this matches Bedrock's design; future work can add `ro` mode or filesystem-level restrictions.
- **[LocalEnvironment bind mount only works when sandbox runs on same host]** In a multi-host deployment, the host path won't exist on the sandbox machine. → Mitigation: this is a known limitation; multi-host deployments must use DockerEnvironment.
- **[SandboxExecutor uses `docker run` CLI, not aiodocker]** Volume args must be formatted as CLI strings. → Mitigation: straightforward string formatting in `_create_container()`.
- **[No sandbox spec exists to modify]** The proposal lists `sandbox-executor` as a modified capability, but no main spec exists. → Mitigation: create the `sandbox-environment-mount` new capability spec only; the sandbox-executor change is implementation-only.

## Migration Plan

No migration required. This is purely additive:
1. `SandboxConfig.volumes` defaults to `{}` — no volume mounts when not specified.
2. Existing `BuiltinTools._execute_code()` callers continue to work unchanged.
3. Environment mount is opt-in: only activated when an AgentEnvironment is available.

**Rollback:** Remove the `volumes` field from SandboxConfig. Sandbox containers revert to no-mount behavior.

## Open Questions

None — all design decisions are resolved during exploration.
