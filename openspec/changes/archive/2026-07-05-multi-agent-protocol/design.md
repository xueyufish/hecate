## Context

Hecate has a mature multi-agent stack: 6 collaboration patterns (Sequential, Parallel, Handoff, Broadcast, Negotiation, Debate), EventBus pub/sub, TaskAllocator, P2P Negotiator, Agent-as-Tool via EnginePort. All communication is process-local — there is no protocol layer for cross-platform agent discovery and delegation.

The A2A protocol (v1.2, March 2026) is the industry standard: 150+ organizations in production, 5 official SDKs, Linux Foundation governance. Hecate's MCP client/server bidirectional architecture proves that protocol-layer integration works well. A2A is complementary to MCP: A2A handles agent-to-agent (horizontal), MCP handles agent-to-tool (vertical).

The skill/tool/knowledge/workflow associations are fragmented across 4 different patterns (names, names, UUIDs, single-UUID). No industry platform has unified these as a single "Skill" abstraction — Hecate can differentiate here.

## Goals / Non-Goals

**Goals:**
- A2A v1.2 protocol compliance: Hecate as both A2A server and client
- Signed Agent Cards with JWS(ES256) + RFC 8785 + JWKS
- Unified SkillRegistry abstracting 5 resource types (Tool, Skill, KB, Workflow, Agent) as SkillRef
- Bidirectional Agent ↔ Workflow embedding with max_depth=3
- Extended conflict handling for distributed/A2A scenarios
- Zero data migration for skill unification (SkillRegistry reads existing tables)

**Non-Goals:**
- gRPC transport binding (defer to follow-up; JSON-RPC over HTTP + SSE is MVP)
- A2A REST endpoint equivalents (JSON-RPC is MVP; REST is follow-up)
- AGNTCY Directory integration (separate future change)
- AP2 (Agent Payments Protocol) extension (P5 scope)
- OAuth2 flow support (APIKey + HTTP Bearer only for MVP; OAuth2/OIDC/mTLS deferred)
- `ListTasks` pagination (defer; basic task lifecycle is MVP)
- Custom A2A extensions (defer; standard spec only for MVP)
- UI/Canvas changes for skill registry or A2A (backend only this change)

## Decisions

### Decision 1: Use official `a2a-sdk` Python package

**Choice**: Use `a2a-sdk` (official Python SDK from a2aproject/a2a-python)

**Alternatives**:
- *Implement from scratch* (like engine layer philosophy): More work (~2000 LOC), must track spec changes manually, risk of protocol non-compliance.
- *Vendor the SDK*: Fork maintenance burden.

**Rationale**: MCP client already uses official `mcp` SDK — precedent for using protocol SDKs at the services layer. The `a2a-sdk` is production-ready with FastAPI integration (`A2AFastAPIApplication`), task lifecycle management, SSE streaming, and signature support. Keeps Hecate on the spec-compliant path without reinventing protocol machinery. The SDK lives at the services layer, not the engine layer — engine remains zero-external-deps.

### Decision 2: SkillRegistry as service layer, not new model

**Choice**: `SkillRegistry` reads from existing SkillModel, ToolModel, KnowledgeBaseModel, WorkflowModel, AgentModel — no new UnifiedSkillModel table.

**Alternatives**:
- *New UnifiedSkillModel table*: Single source of truth but requires data migration, creates sync/duplication issues, breaks existing CRUD APIs.
- *Python Protocol/Union type*: Type-safe but runtime resolution still needed; doesn't solve the persistence problem.

**Rationale**: Zero migration risk. Existing CRUD APIs keep working unchanged. SkillRegistry is a read-side abstraction that unifies at the service layer. The trade-off is cross-table resolution logic in the registry, which is acceptable.

### Decision 3: A2A module structure mirrors MCP

**Choice**: `src/hecate/a2a/` module with `server/`, `client/`, `signing.py`, `types.py` submodules.

**Rationale**: Mirrors the proven `src/hecate/services/mcp/` structure. Clear separation between server (Hecate as remote agent) and client (Hecate calling remote agents). Signing is shared across both.

### Decision 4: Workflow embedding via EnginePort extension

**Choice**: Extend `EnginePort` with optional `workflow_execute()` method (default NotImplementedError, like `agent_execute()`). Reuse existing PregelRuntime for execution.

**Alternatives**:
- *New WorkflowSkill wrapper class*: More indirection, duplicates AgentTool pattern.
- *Subgraph composition (LangGraph style)*: Complex state sharing, hard to debug.

**Rationale**: Consistent with existing Agent-as-Tool pattern. AgentTool wraps AgentDefinition; similarly, WorkflowTool wraps workflow_id. Both delegate to EnginePort methods. max_depth=3 enforced via context stack.

### Decision 5: Conflict handling extends existing ConflictResolver

**Choice**: Add distributed conflict modes to existing `ConflictResolver` (resource, task, state, permission) rather than creating a new class.

**Rationale**: ConflictResolver already has 4 strategies (LWW, MERGE_LIST, MERGE_MAP, HUMAN_APPROVAL). Extending with distributed modes (distributed lock, negotiation, escalation) is additive and backward-compatible.

### Decision 6: Signing keys stored in DB with rotation support

**Choice**: `AgentCardKeyModel` table stores key pairs (kid, private_key, public_key, algorithm, created_at, rotated_at). Rotation via new key generation + old key grace period.

**Alternatives**:
- *File-based keys*: Harder to rotate in multi-replica deployments.
- *Vault integration*: Overkill for MVP; can integrate with existing SecretProviderABC later.

**Rationale**: DB-based keys work with multi-replica deployments (all replicas read from same DB). Grace period allows in-flight verifications to complete during rotation.

## Risks / Trade-offs

- **[a2a-sdk version churn]** → Pin to specific version in pyproject.toml; track spec updates quarterly.
- **[SkillRegistry cross-table queries]** → Denormalize skill index into a lightweight view or cache; accept eventual consistency.
- **[Workflow nesting depth >3 degrades reasoning]** → Enforce max_depth=3 at EnginePort level with clear error message; log warnings at depth=2.
- **[A2A task lifecycle persistence]** → Use existing async SQLAlchemy + PostgreSQL; InMemoryTaskStore for tests only.
- **[Signed card key compromise]** → Key rotation API + grace period; alert on signature verification failure rate spike.
- **[Protocol spec drift during implementation]** → Target v1.2 (stable since March 2026); avoid bleeding-edge features.

## Migration Plan

1. **Phase 1 — Skill Registry (backward compatible)**: Add SkillRegistry service reading existing tables. Add `agent.skill_ids` JSON field (list of SkillRef) alongside existing `tools`/`skills`/`knowledge_base_ids` fields. Old fields remain authoritative; new field is opt-in.
2. **Phase 2 — Workflow Embedding**: Extend EnginePort with `workflow_execute()`. Add WorkflowTool wrapper. No migration needed — purely additive.
3. **Phase 3 — A2A Server**: New endpoints at `/.well-known/agent-card.json` and `/a2a/` (JSON-RPC). No impact on existing APIs. Migration adds `a2a_tasks` table.
4. **Phase 4 — A2A Client**: New A2AClient class. No migration. Adds `remote_agents` config for trusted external A2A endpoints.
5. **Phase 5 — Signed Cards**: New `agent_card_keys` table. Signing is opt-in per workspace. Unsigned cards work but log WARNING.
6. **Phase 6 — Conflict Handling**: Extend ConflictResolver with distributed modes. Existing 4 strategies unchanged.

**Rollback**: Each phase is independently deployable. A2A server can be disabled via config flag (`A2A_SERVER_ENABLED=false`). SkillRegistry is read-only — disabling it falls back to existing field-based association.

## Open Questions

- **A2A task persistence**: Use existing PostgreSQL or separate A2A-specific database? → Default: same database, separate `a2a_tasks` table.
- **SkillRegistry caching**: In-memory cache with TTL or Redis? → Default: in-memory for MVP, Redis for production (consistent with existing patterns).
- **AgentCard generation**: Static config or dynamic per-agent? → Default: hybrid — base card from config, skills array from agent's associated skills.
