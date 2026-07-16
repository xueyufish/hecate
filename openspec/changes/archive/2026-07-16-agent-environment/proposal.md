## Why

Hecate's agent execution data is scattered across 4 storage backends (PostgreSQL, MinIO, Qdrant, filesystem) with no unified abstraction. Agents cannot manage their own files, memory is not workspace-scoped, and there's no lifecycle management for agent execution environments. Research across 14 platforms (Bedrock AgentCore, AgentScope, Dify, Claude Code, Google Gemini, Salesforce Agentforce, Palantir AIP, Huawei AgentArts) shows that all mature platforms have a unified agent execution environment abstraction. This feature introduces `AgentEnvironment` — the agent's persistent execution context — distinct from `WorkspaceModel` (multi-tenant isolation boundary).

## What Changes

- **AgentEnvironment ABC**: Unified abstraction for agent execution environment with file management, lifecycle, and session association. `AgentEnvironment` (execution context) is conceptually distinct from `WorkspaceModel` (tenant isolation boundary).
- **LocalEnvironment**: Filesystem-backed implementation storing agent data at `{WORKSPACE_ROOT}/{agent_id}/` with subdirectories: `sessions/`, `files/`, `memory/`, `skills/`.
- **EnvironmentManager**: Lifecycle manager with lazy creation, TTL-based eviction (24h default, resets on each interaction), and multi-tenant cache.
- **File CRUD API**: REST endpoints for listing, reading, writing, and deleting files in an agent's environment.
- **Session auto-association**: Sessions automatically associate with the agent's environment via `agent_id` — no manual environment ID management needed.
- **WorkflowExecutionService integration**: Environment is created lazily on first use, info passed via `execution_context` to workers.

**Naming rationale**: "AgentEnvironment" (not "AgentWorkspace") to avoid conceptual collision with `WorkspaceModel` (multi-tenant isolation boundary). Industry comparison: Dify uses "Tenant" + "AgentRuntimeSession", Bedrock uses "Tenant" + "Agent Runtime", AgentScope uses "Workspace" (but has no tenant model).

## Capabilities

### New Capabilities

- `agent-environment`: AgentEnvironment ABC, LocalEnvironment, EnvironmentManager (TTL eviction), file CRUD API, session auto-association, WorkflowExecutionService integration

### Modified Capabilities

- _(none — this is additive; existing session/conversation system unchanged)_

## Impact

- **New files**:
  - `src/hecate/services/environment/__init__.py`
  - `src/hecate/services/environment/environment.py` — AgentEnvironment ABC + LocalEnvironment
  - `src/hecate/services/environment/manager.py` — EnvironmentManager
  - `src/hecate/api/management/environment.py` — REST API
  - `tests/test_services/test_environment/` — tests
- **Modified files**:
  - `src/hecate/core/config.py` — new settings: `AGENT_ENV_TTL`, `AGENT_ENV_ENABLED`
  - `src/hecate/main.py` — register environment router
- **Dependencies**: None new
- **Migration**: None (no DB model changes in MVP)
