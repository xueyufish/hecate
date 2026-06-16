## Context

Hecate operates in single-tenant mode: all resources share a zero-UUID workspace, API keys are comma-separated environment variables, JWT tokens carry only `sub` (user_id), and there is no organization or role concept. Seven models already have a `workspace_id` column (plain UUID, no FK, defaulting to zero UUID) but it is never enforced by the auth layer.

The auth stack is:
- **JWT**: HS256, 30min access / 7d refresh, claims = `{sub, type, exp, iat}`. Secret derived from first API key.
- **API Key**: `HECATE_API_KEYS` env var → `settings.api_keys_list`. Checked as plain string match in `verify_api_key()`. API key auth returns placeholder UUID `00000000-...` for user_id.
- **Dependency injection**: `verify_api_key()` (dual JWT+key), `get_current_user_id()` (extract sub or return placeholder), `get_current_agent()` (404 lookup).
- **No RBAC**: Every authenticated request has full access to all resources.

External reference points:
- **Dify**: `ApiToken` with `tenant_id` (required) + optional `app_id` for finer scoping. Simple 2-level key model.
- **AgentScope**: Multi-tenant via `user_id` routing, `LocalWorkspaceManager` per-agent directory isolation. No org hierarchy.
- **Salesforce Agentforce**: Inherits platform IAM (org → user → permission set). Too heavy for self-hosted.
- **openJiuwen**: AK/SK + Channel Token at gateway, session-affinity scheduling for multi-tenant isolation. Org hierarchy managed externally.

Design principle: Hecate does not manage organizational hierarchy (departments, teams). Org structure is managed by external OA/IAM systems and synced into Hecate. Hecate provides the isolation boundary (workspace) and access control (role), not the org chart.

## Goals / Non-Goals

**Goals:**

- Establish flat org → workspace hierarchy as the multi-tenant isolation model.
- Implement workspace-level RBAC with 3 roles (admin, editor, viewer).
- Replace env-var API keys with database-backed, scoped API keys (system + workspace).
- Enrich JWT tokens with org/workspace/role claims for downstream authorization.
- Add workspace FK constraints to existing models and enforce workspace-scoped queries.
- Provide backward-compatible bootstrap (default org + default workspace with zero-UUID IDs).
- Preserve extension points for future SSO (OIDC/SAML) and team/group layer.

**Non-Goals:**

- Nested org hierarchy (departments, sub-departments). Not Hecate's responsibility.
- Team/group model between org and workspace. Can be added as pure increment later.
- SSO integration (OIDC/SAML). Deferred to a future change; current design adds `sso_id` field as extension point only.
- Fine-grained resource-level permissions (e.g., "can edit agents but not workflows"). Workspace-level 3-role model is sufficient for now.
- Tenant isolation at compute/network level (feature 10.5). This change covers data isolation only.
- Rate limiting per workspace. Current per-token rate limiting remains unchanged.

## Decisions

### D1: Flat Org → Workspace hierarchy

**Decision**: Two-level model: Organization (enterprise customer) → Workspace (resource isolation unit).

**Alternatives considered**:
- **Nested org (org → sub-org → workspace)**: Adds recursive queries, permission inheritance complexity. Org hierarchy is not Hecate's responsibility — managed by external OA/IAM.
- **Org + Team (org → team → workspace)**: Additional layer for department grouping. YAGNI — no current use case requires it. Adding a Team layer later is pure increment (no schema changes to existing tables).

**Rationale**: Flat model matches industry patterns (Dify tenant → app, Coze space → project). Department hierarchy from OA/IAM is flattened into workspace mappings at sync time.

### D2: Workspace as the isolation boundary

**Decision**: All tenant-scoped resources belong to exactly one workspace. workspace_id on existing models gains FK to WorkspaceModel.

**Rationale**: 7 models already have workspace_id columns. The zero-UUID default was a placeholder for P1. Promoting it to a real FK with enforcement is the minimal change for multi-tenancy.

**Impact on existing models**: AgentModel, WorkflowModel, SkillModel, ToolModel, KnowledgeBaseModel, PromptModel, MemoryBlockModel, MemoryModel, KnowledgeMemoryModel — all gain FK constraint. Existing rows with zero-UUID workspace_id are assigned to the default workspace via bootstrap migration.

### D3: 3-role RBAC at workspace level

**Decision**: WorkspaceMemberModel with enum role: `admin` (manage workspace + members + all resources), `editor` (create/edit/delete resources), `viewer` (read-only access).

**Alternatives considered**:
- **2-role (owner/member)**: Too coarse for enterprise. Viewer role is essential for audit/compliance.
- **Custom role builder**: Over-engineering for current needs. 3 fixed roles cover 90% of use cases.

**Permission matrix**:

| Action | admin | editor | viewer |
|--------|-------|--------|--------|
| Manage workspace settings | ✅ | ❌ | ❌ |
| Manage members (invite/remove) | ✅ | ❌ | ❌ |
| Create resources | ✅ | ✅ | ❌ |
| Edit/delete resources | ✅ | ✅ | ❌ |
| Read resources | ✅ | ✅ | ✅ |
| Manage API keys | ✅ | ❌ | ❌ |
| Delete workspace | ✅ | ❌ | ❌ |

### D4: 2-level API key scope

**Decision**: API keys have `scope` enum: `system` (cross-org platform admin) or `workspace` (single workspace). No `org` scope.

**Alternatives considered**:
- **3-level (system/org/workspace)**: Org-level key adds complexity for marginal benefit. Org admins can use JWT auth in the browser.
- **Single level (all keys equal)**: Insufficient for multi-tenant isolation.

**Key storage**: Store SHA-256 hash + 8-char prefix. Original key shown only once at creation time. Matches industry best practice (Dify, GitHub tokens).

**Key format**: `hcat_<base62_random_32chars>` — prefixed for identification, long enough for entropy.

### D5: JWT claims enrichment

**Decision**: Access tokens gain `org_id`, `workspace_id`, `role` claims alongside existing `sub`, `type`, `exp`, `iat`.

**New JWT structure**:
```json
{
  "sub": "user-uuid",
  "type": "access",
  "org_id": "org-uuid",
  "workspace_id": "workspace-uuid",
  "role": "editor",
  "exp": "...",
  "iat": "..."
}
```

**Rationale**: Downstream code needs workspace context without DB lookups on every request. JWT claims provide O(1) access to auth context.

**Login flow change**: `/auth/login` returns tokens scoped to the user's "current" workspace. A new `/auth/switch-workspace` endpoint allows changing the active workspace (issues new tokens).

### D6: Dependency injection redesign

**Decision**: Replace `verify_api_key()` and `get_current_user_id()` with a unified `get_auth_context()` dependency that returns a typed `AuthContext` dataclass.

```python
@dataclass
class AuthContext:
    user_id: uuid.UUID
    org_id: uuid.UUID
    workspace_id: uuid.UUID
    role: WorkspaceRole | None  # None for system-scope API keys
    auth_method: Literal["jwt", "api_key"]
    api_key_scope: ApiKeyScope | None  # None for JWT auth
```

**Migration strategy**: Existing `Depends(verify_api_key)` and `Depends(get_current_user_id)` are replaced by `Depends(get_auth_context)`. Backward-compatible during transition — old deps are kept as thin wrappers during the migration period.

### D7: Bootstrap migration

**Decision**: Migration creates a default org (`slug: "default"`, id: zero-UUID) and default workspace (`slug: "default"`, id: zero-UUID). All existing rows with zero-UUID workspace_id remain valid.

**Rationale**: Zero-downtime migration for single-tenant deployments. Existing data is automatically part of the default workspace. No data backfill needed for the workspace_id columns.

### D8: Organization ownership

**Decision**: Each organization has at least one `owner` — stored as `OrganizationModel.owner_id` (FK to UserModel). The owner is the initial admin of all workspaces under the org.

**Rationale**: Org needs a responsible party for lifecycle management (billing, deletion). Owner is set at org creation and can be transferred.

## Risks / Trade-offs

**[R1] Breaking API key change** → The `HECATE_API_KEYS` env var is deprecated. Existing deployments must create API keys via the new management endpoint. **Mitigation**: Support both env-var and DB keys during a deprecation window (v0.x). Log a warning when env-var keys are used.

**[R2] FK constraint on workspace_id** → Existing rows with zero-UUID must reference a valid workspace. **Mitigation**: Bootstrap migration creates default workspace before adding FK constraints.

**[R3] JWT token size increase** → Adding 3 claims (org_id, workspace_id, role) increases token size by ~120 bytes. Negligible for HTTP headers.

**[R4] Workspace member table as bottleneck** → Every authenticated request needs membership lookup. **Mitigation**: JWT claims embed role, avoiding DB lookup per request. Membership is only checked on token issuance (login, switch-workspace).

**[R5] Single default workspace for backward compat** → If a deployment has multiple users sharing one instance, they all land in the same default workspace. **Mitigation**: This is the current behavior (zero-UUID workspace). Admins can create orgs/workspaces and migrate resources after upgrade.

## Migration Plan

1. **Phase 1 — Schema** (this change): Create new models (Organization, Workspace, WorkspaceMember, ApiKey). Add FK constraints to existing models. Bootstrap default org/workspace.
2. **Phase 2 — Auth enrichment** (this change): Expand JWT claims. Implement `get_auth_context()`. Update all API endpoints to use workspace-scoped queries.
3. **Phase 3 — Deprecation** (next release): Remove `HECATE_API_KEYS` env var support. Remove old `verify_api_key()` / `get_current_user_id()`.

**Rollback**: Migration is reversible — downgrade drops new tables and FK constraints, restores zero-UUID defaults.

## Open Questions

- **Workspace switching UX**: Should `/auth/switch-workspace` accept a workspace UUID directly, or should we support "switch to org X" (listing available workspaces)? Leaning toward explicit workspace UUID for simplicity.
- **API key rotation**: Should key rotation create a new key immediately (invalidating old) or allow a grace period with two active keys? Leaning toward immediate rotation for security.
- **System API key ownership**: System-scope keys bypass workspace membership checks. Should they be restricted to a special "system" org or managed separately? Leaning toward no org association for system keys.
