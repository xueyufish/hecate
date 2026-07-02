## Why

Hecate currently operates in single-workspace mode — all resources share a zero-UUID workspace, API keys are unscoped environment variables, and there is no organization or role model. This blocks enterprise adoption: customers need multi-tenant isolation, workspace-scoped access control, and auditable API key management. The current auth system (JWT with only `sub` claim + global API keys) cannot express "this user can access workspace X as editor" or "this API key is scoped to workspace Y."

This change combines features 10.1 (Organization Management) and 10.2 (RBAC) because org structure and access control are tightly coupled — you cannot enforce workspace-level roles without first establishing the organization → workspace hierarchy.

## What Changes

- **Organization model**: New `OrganizationModel` (id, name, slug, settings) representing an enterprise customer. Flat structure — no nested departments. Department hierarchy is managed by external OA/IAM systems and synced into Hecate as workspace-level mappings.
- **Workspace model**: New `WorkspaceModel` (id, org_id FK, name, slug, settings) as the resource isolation boundary. Each workspace belongs to exactly one organization.
- **Workspace membership**: New `WorkspaceMemberModel` (user_id, workspace_id, role) with three roles: `admin`, `editor`, `viewer`. This replaces the implicit "everyone can access everything" model.
- **API key management**: New `ApiKeyModel` (key_hash, key_prefix, scope, org_id?, workspace_id?) stored in the database. Two scopes: `system` (cross-org platform admin) and `workspace` (single-workspace operations). **BREAKING**: replaces the current `HECATE_API_KEYS` environment variable approach.
- **JWT claims expansion**: Access tokens gain `org_id`, `workspace_id`, and `role` claims. The current `sub`-only JWT is insufficient for workspace-scoped authorization.
- **Dependency injection enrichment**: `verify_api_key()` and `get_current_user_id()` gain workspace resolution — downstream code receives authenticated context (org, workspace, role) instead of a bare user ID or token string.
- **Workspace enforcement**: All 7 existing models with `workspace_id` (Agent, Workflow, Skill, Tool, KnowledgeBase, Prompt, Memory*) gain FK constraints and are filtered by authenticated workspace context.

## Capabilities

### New Capabilities

- `org-workspace`: Organization and workspace CRUD, lifecycle management (create, read, update, soft-delete), slug-based addressing, and workspace settings. Covers OrganizationModel, WorkspaceModel, their API endpoints, and service layer.
- `rbac`: Workspace-level role-based access control with three roles (admin, editor, viewer). Covers WorkspaceMemberModel, role assignment/revocation, permission checks via FastAPI dependencies, and role-based query filtering.
- `api-key-management`: Database-backed API key lifecycle — creation, rotation, revocation, scoping (system/workspace), last-used tracking, and optional expiration. Replaces the env-var `HECATE_API_KEYS` approach.
- `auth-enhancement`: JWT claims expansion (org_id, workspace_id, role), token refresh with workspace context, login flow returning accessible workspaces, and SSO extension points for future OIDC/SAML integration.

### Modified Capabilities

- `memory-api`: JWT/workspace auth context must be threaded into memory endpoints so that memory blocks are scoped to the authenticated workspace rather than accepting any workspace_id in the request body.
- `session-memory`: Session memory tools must auto-inject workspace_id from auth context rather than from request parameters.

## Impact

**Models** (new): OrganizationModel, WorkspaceModel, WorkspaceMemberModel, ApiKeyModel — 4 new ORM models, 4 new Alembic migrations.

**Models** (modified): 7 existing models (Agent, Workflow, Skill, Tool, KnowledgeBase, Prompt, Memory*) gain FK constraints on workspace_id referencing WorkspaceModel. Requires data migration for existing rows (all currently zero-UUID — must be assigned to a default workspace).

**Core**: `deps.py` (verify_api_key, get_current_user_id rewritten), `config.py` (deprecate HECATE_API_KEYS env var, add default workspace/org bootstrap).

**Services**: `auth/service.py` and `auth/token.py` gain workspace-aware login and token creation. New `OrganizationService`, `WorkspaceService`, `ApiKeyService`.

**API**: New routers — `/api/orgs`, `/api/workspaces`, `/api/api-keys`. Modified routers — `/api/agents`, `/api/workflows`, `/api/skills`, `/api/tools`, `/api/knowledge-bases`, `/api/prompts`, `/api/memory` gain workspace-scoped filtering.

**Tests**: New test files for org, workspace, RBAC, API key flows. Existing auth tests updated for new JWT claims.

**Backward compatibility**: A bootstrap migration creates a default org + default workspace with zero-UUID IDs, ensuring existing single-tenant deployments continue working without configuration changes.
