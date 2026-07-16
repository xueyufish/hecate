## Context

Hecate has a `WorkspaceModel` for multi-tenant isolation (Organization → Workspace → Agents/Tools/KBs), but no unified abstraction for the agent's execution environment. Agent data (files, memory, session logs) is scattered across PostgreSQL, MinIO, Qdrant, and filesystem with no lifecycle management.

**Research basis** (14 platforms):
- Bedrock AgentCore: Agent Runtime + Managed Session Storage (14-day TTL, per-session microVM)
- AgentScope: Workspace ABC (Local/Docker/E2B), WorkspaceManager with TTL eviction, per-agent isolation
- Dify: AgentRuntimeSession + AgentDrive (per-agent file system)
- Claude Code: Working Directory + SessionStore adapter (S3/Redis/Postgres)
- Google Gemini: SandboxEnvironment (7-day TTL, env_id reuse)
- Salesforce: Session-scoped (explicit create/delete)
- Palantir AIP: Ontology as unified memory (48h node lifetime)
- Huawei AgentArts: Runtime SDK + Memory Bank (independent services)

## Goals / Non-Goals

**Goals:**
- AgentEnvironment ABC with LocalEnvironment (filesystem) implementation
- EnvironmentManager for lifecycle (create/get/close with TTL eviction)
- File CRUD API (list/read/write/delete)
- Session auto-association (via agent_id, no manual env_id)
- Lazy creation (first file operation)
- Configurable TTL (default 24h, resets on each interaction)
- Service layer only (no engine layer changes, no EnginePort changes)

**Non-Goals:**
- DockerEnvironment / E2BEnvironment (1.3.15a, separate feature)
- Context offloading (1.3.15b, separate feature)
- Sandbox environment mount (1.3.15c, separate feature)
- AgentState separation (1.3.16, separate feature)
- New DB model (no EnvironmentModel in MVP)
- EnginePort changes (workers don't directly access environment)

## Decisions

### Decision 1: AgentEnvironment, not AgentWorkspace

**Choice**: Name the execution environment "AgentEnvironment" to avoid collision with `WorkspaceModel` (multi-tenant isolation boundary).

**Rationale**: Industry research shows no platform uses the same term for both tenant isolation and execution environment. Hecate already uses "Workspace" for tenant isolation (`WorkspaceModel`). Using "AgentWorkspace" would create conceptual confusion.

### Decision 2: Service layer, not engine layer

**Choice**: AgentEnvironment lives in `services/environment/`, not `engine/`.

**Rationale**: All platforms manage execution environments at the service layer (AgentScope: Agent Service, Bedrock: AgentCore Runtime, Dify: Agent Runtime). The engine layer (agent loop) consumes the environment but doesn't manage it. This avoids changing EnginePort and keeps the engine layer's zero-external-deps principle intact.

### Decision 3: Per-agent isolation, not per-session

**Choice**: Environment is keyed by `agent_id`. All sessions of the same agent share one environment.

**Rationale**: AgentScope's built-in managers all isolate by `agent_id`. Per-session isolation is handled by 1.3.16 Agent State Separation (volatile state). Environment = durable (per-agent), AgentState = volatile (per-session).

### Decision 4: Lazy creation with TTL eviction

**Choice**: Environment directory created on first file operation, not on agent creation. TTL default 24h, resets on each interaction.

**Rationale**: All platforms use lazy creation (Google, Bedrock, AgentScope). 24h TTL is between AgentScope (1h) and Bedrock (14d). TTL resets on each interaction (Google pattern) so active agents never get evicted.

### Decision 5: No EnginePort changes

**Choice**: Workers don't directly access the environment. Environment info is passed via `execution_context` dict.

**Rationale**: MVP scope. Workers access tools/knowledge via EnginePort, but environment is primarily for file management (API-driven) and lifecycle management. 1.3.15c (Sandbox Environment Mount) will add EnginePort integration later.

## Risks / Trade-offs

- **[Single-instance only]** — LocalEnvironment uses filesystem, not suitable for multi-instance deployment. Mitigation: abstract behind AgentEnvironment ABC; MinIO/S3 backend can be added later.

- **[No DB model]** — No metadata about environment (size, creation time). Mitigation: can add EnvironmentModel later if needed for admin UI.

- **[TTL eviction race]** — Two concurrent requests could race on eviction. Mitigation: EnvironmentManager uses asyncio.Lock for thread safety.
