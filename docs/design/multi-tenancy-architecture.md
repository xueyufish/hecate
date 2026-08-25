# Multi-Tenancy Architecture

Deep-dive design document for Hecate's tenant isolation model: Organization → Workspace → User, with role-based access control, SCIM provisioning, and audit boundaries. For the decision rationale, see [ADR-018: Zero Trust Identity Architecture](adr/018-zero-trust-identity-architecture.md). For operational recipes, see [Configure SSO and SCIM](../how-to/configure-sso-scim.md).

This document is for **architects** designing multi-tenant deployments and **integrators** connecting Hecate to enterprise identity systems (Okta, Azure AD, Keycloak).

---

## The three-tier tenant model

Hecate uses a **three-tier** tenancy model:

```
┌─────────────────────────────────────────────────────────────────────┐
│                                                                     │
│   Organization                                                      │
│   ├── Represents an enterprise customer                              │
│   ├── Has 1+ workspaces                                              │
│   ├── Has 1+ users (with global role in the org)                     │
│   └── Is the billing / SLA boundary                                  │
│                                                                     │
│         └── Workspace                                                │
│             ├── Resource isolation boundary                          │
│             ├── All tenant-scoped resources (agents, KBs, etc.)      │
│             │   belong to exactly one workspace                     │
│             ├── Has 1+ members with workspace-level roles           │
│             └── Is the RBAC boundary                                │
│                                                                     │
│                   └── User                                          │
│                       ├── Identified by email or external ID        │
│                       ├── Can be member of multiple workspaces      │
│                       ├── Each membership has a WorkspaceRole        │
│                       └── Authenticated by Hecate JWT or SSO token   │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
```

### Why three tiers (not two)

| Tier | Boundary | Question answered |
|---|---|---|
| Organization | Billing / SLA | "Which customer is this?" |
| Workspace | RBAC / Resource isolation | "Which team / project can access this?" |
| User | Identity | "Who is this person?" |

A two-tier model (Org → User) would force either:

- Resource isolation at the Org level (too coarse — different teams need different resources)
- No resource isolation (everyone sees everything — not viable for multi-tenant SaaS)

A four-tier model (Org → Dept → Workspace → User) would add complexity without clear value — departments are usually managed in the external IAM (Okta, AD) and mapped to workspaces.

---

## Implementation

### Models

The core tenant models are in `src/hecate/models/`:

```python
# src/hecate/models/organization.py
class OrganizationModel(BaseModel):
    """ORM model for organizations — represents an enterprise customer.
    
    Flat structure: no nested departments. Department hierarchy is managed
    by external OA/IAM systems and synced into Hecate as workspace mappings.
    """
    __tablename__ = "organizations"
    
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)
    owner_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    settings: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)


# src/hecate/models/workspace.py
class WorkspaceModel(BaseModel):
    """ORM model for workspaces — the resource isolation boundary.
    
    Each workspace belongs to an organization. All tenant-scoped resources
    (agents, workflows, skills, tools, knowledge bases, prompts, memories)
    belong to exactly one workspace.
    """
    __tablename__ = "workspaces"
    
    org_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(100), nullable=False)
    # ... settings, quotas, billing fields
```

### Tenant isolation count

Hecate's tenant isolation works because **many models carry `workspace_id`** (verified via `grep -rl workspace_id src/hecate/models/*.py`.; the count grows as new tenant-scoped entities land). Every query against a tenant-scoped resource filters by `workspace_id`. Examples:

```
src/hecate/models/agent.py:                 workspace_id
src/hecate/models/agent_card_key.py:        workspace_id
src/hecate/models/alert.py:                 workspace_id
src/hecate/models/api_key.py:               workspace_id
src/hecate/models/approval.py:              workspace_id
src/hecate/models/audit.py:                 workspace_id
src/hecate/models/budget.py:                workspace_id
src/hecate/models/checkpoint.py:            workspace_id
src/hecate/models/conversation.py:          workspace_id
src/hecate/models/dataset.py:               workspace_id
src/hecate/models/dlp.py:                    workspace_id
src/hecate/models/document.py:              workspace_id
src/hecate/models/evaluation.py:            workspace_id
src/hecate/models/evidence.py:              workspace_id
src/hecate/models/fine_tuning_job.py:       workspace_id
src/hecate/models/hook_config.py:           workspace_id
src/hecate/models/inference_endpoint.py:    workspace_id
src/hecate/models/knowledge.py:             workspace_id
src/hecate/models/memory.py:                workspace_id
src/hecate/models/message.py:               workspace_id
... (16 more)
```

This list is verified by grep — any new tenant-scoped resource MUST add `workspace_id` to the model. There is no `org_id` field on tenant-scoped resources; all isolation flows through `workspace_id` (with implicit org_id via the workspace).

### Membership model

```python
# src/hecate/models/workspace_member.py
class WorkspaceRole(enum.StrEnum):
    ADMIN = "admin"      # Full control: add/remove members, edit anything, delete workspace
    EDITOR = "editor"    # Create/edit resources, run agents, view audit
    VIEWER = "viewer"    # Read-only access to resources

class WorkspaceMemberModel(BaseModel):
    """ORM model for workspace membership.
    
    Each entry links a user to a workspace with a specific role.
    A user can be a member of multiple workspaces with different roles.
    """
    __tablename__ = "workspace_members"
    
    user_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    workspace_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    role: Mapped[WorkspaceRole] = mapped_column(...)
```

A user can be a member of multiple workspaces with **different roles** in each. For example, Alice might be `ADMIN` in workspace "engineering" and `VIEWER` in workspace "marketing".

---

## Authentication providers

Hecate ships five authentication providers in `src/hecate/auth/`:

| Provider | Use case | How to enable |
|---|---|---|
| **API Key** (`api_key_provider.py`) | Service-to-service, CI/CD, scripts | Generate via `hecate api-key create` |
| **JWT** (`jwt_provider.py`) | First-party web clients (canvas, CLI) | Default; users get a JWT on login |
| **OIDC** (`oidc_provider.py`) | Enterprise SSO via Okta / Azure AD / Google | Set `OIDC_ISSUER_URL` + `OIDC_CLIENT_ID/SECRET` |
| **SAML** (`saml_provider.py`) | Enterprise SSO via legacy IdPs (ADFS) | Set `SAML_IDP_METADATA_URL` |
| **LDAP** (`ldap_provider.py`) | Direct LDAP bind for legacy environments | Set `LDAP_URL` + `LDAP_BIND_DN` |

### AuthProvider ABC

```python
# src/hecate/auth/provider.py
class AuthProviderBase(ABC):
    @property
    @abstractmethod
    def name(self) -> str: ...  # "jwt", "api_key", "oidc", "saml", "ldap"
    
    @property
    @abstractmethod
    def description(self) -> str: ...
    
    @abstractmethod
    async def authenticate(self, token: str, db: AsyncSession) -> AuthContext | None: ...
```

Returns `AuthContext` (user_id, org_id, workspace_ids[], scopes[]) or `None` if auth fails.

### How auth flows

```
1. Request arrives with credentials:
   - Authorization: Bearer <jwt>     (JWT)
   - X-API-Key: <key>                (API key)
   - OIDC: Authorization Code Flow   (SSO)
   - SAML: SAMLResponse POST         (SSO)
   - LDAP: simple bind over TLS      (direct)

2. AuthProviderBase.authenticate(token) → AuthContext | None

3. AuthContext attaches to request state (FastAPI Depends)

4. RBAC check: does this AuthContext's role permit this action on this resource?
   - workspace_id of resource == workspace_id of user's membership? → permit
   - role >= required role for the action? → permit
   - otherwise → 403 Forbidden

5. AuditWriter logs the auth attempt (success or failure) to audit_logs
```

---

## SSO configuration

For enterprise deployments, configure SSO via OIDC or SAML. The flow:

### OIDC setup (Okta / Azure AD / Google Workspace)

```bash
# .env
OIDC_ISSUER_URL=https://your-tenant.okta.com/oauth2/default
OIDC_CLIENT_ID=<from-okta-app-config>
OIDC_CLIENT_SECRET=<from-okta-app-config>
OIDC_REDIRECT_URI=https://hecate.example.com/auth/oidc/callback

# Optional: map OIDC claims to Hecate workspaces
OIDC_CLAIM_MAPPING={"email": "user_email", "groups": "workspace_slugs"}
```

### SAML setup (legacy AD / ADFS)

```bash
# .env
SAML_IDP_METADATA_URL=https://adfs.example.com/federationmetadata/2007-06/federationmetadata.xml
SAML_SP_ENTITY_ID=https://hecate.example.com/saml
SAML_CLAIM_MAPPING={"email": "user_email", "group": "workspace_slugs"}
```

### Claim → Workspace mapping

Hecate maps OIDC/SAML claims to workspace membership automatically:

```yaml
# Okta group "hecate-eng-team" → Hecate workspace "engineering" with role "admin"
# Okta group "hecate-marketing" → Hecate workspace "marketing" with role "viewer"
```

This is configured via `OIDC_CLAIM_MAPPING` / `SAML_CLAIM_MAPPING`. When a user logs in, Hecate creates workspace memberships based on their group memberships.

---

## SCIM provisioning

For automated user/workspace provisioning (the enterprise standard), Hecate ships a SCIM 2.0 endpoint in `src/hecate/scim/`:

- **Inbound SCIM** — Okta / Azure AD pushes user/group changes to Hecate
- **Outbound SCIM** (P3) — Hecate pushes changes to downstream systems

The SCIM endpoint follows the [RFC 7644](https://datatracker.ietf.org/doc/html/rfc7644) spec.

### Typical setup

```
Okta ──SCIM push──▶ Hecate /scim/v2/Users
                  Hecate /scim/v2/Groups

When a user is created in Okta:
  - SCIM POST to Hecate creates the user
  - User's Okta groups → Hecate workspace memberships (via claim mapping)
  - When user is removed from Okta → Hecate removes their workspace memberships (soft delete)
```

This means Hecate's user/workspace lifecycle is managed **centrally** from Okta/AD — no manual user creation in Hecate.

---

## RBAC matrix

Three roles × action categories:

| Action | ADMIN | EDITOR | VIEWER |
|---|---|---|---|
| View resources (agents, KBs, workflows) | ✅ | ✅ | ✅ |
| Create / edit resources | ✅ | ✅ | ❌ |
| Delete resources | ✅ | ❌ | ❌ |
| Run agent invocations | ✅ | ✅ | ✅ |
| View audit logs | ✅ | ✅ (own actions only) | ❌ |
| View cost / quota | ✅ | ✅ | ❌ |
| Add / remove workspace members | ✅ | ❌ | ❌ |
| Change workspace settings | ✅ | ❌ | ❌ |
| Delete workspace | ✅ (org owner only) | ❌ | ❌ |

Cross-workspace actions (e.g., copying an agent from workspace A to B) require `ADMIN` in the source workspace **and** `EDITOR` (or higher) in the destination.

---

## Cross-workspace operations

Most operations are workspace-scoped. A few span workspaces:

| Operation | Required |
|---|---|
| Copy agent from workspace A → B | ADMIN or EDITOR in A + EDITOR in B |
| Share KB across workspaces (read-only) | ADMIN in A + VIEWER or higher in B |
| Move user between workspaces | ADMIN in source + ADMIN in destination |
| Bulk-import agents to new workspace | EDITOR in destination |

Cross-workspace operations generate audit events tagged with both `source_workspace_id` and `destination_workspace_id` for compliance tracking.

---

## Quotas and rate limits

Per-workspace quotas are enforced at the API gateway and at the engine:

| Resource | Default quota | Configurable via |
|---|---|---|
| Concurrent sessions | 100 | `WORKSPACE_MAX_SESSIONS` |
| Daily LLM tokens | 10M | `WORKSPACE_DAILY_TOKEN_LIMIT` |
| Daily cost (USD) | $100 | `WORKSPACE_DAILY_COST_LIMIT` |
| Storage (KBs + checkpoints) | 100 GB | `WORKSPACE_STORAGE_LIMIT` |
| Number of agents | 100 | `WORKSPACE_AGENT_LIMIT` |

Limits hit return HTTP 429 with `Retry-After` and the quota name in the response body.

Organization-level quotas (aggregate across workspaces) are also supported — see the [Budget API](../reference/rest-api.md#ops-center-monitoring-and-cost).

---

## Audit boundary

Every action that crosses a tenant boundary is audited:

| Action | Audit event |
|---|---|
| User from org A logs in for the first time | `auth.cross_org_login` |
| User added to workspace | `workspace.member_added` |
| User removed from workspace | `workspace.member_removed` |
| Role changed | `workspace.role_changed` |
| Cross-workspace resource copy | `resource.cross_workspace_copy` |
| Workspace deleted | `workspace.deleted` (with all members' audit trail retained) |

These events are tagged with both `org_id` and `workspace_id` for compliance reporting.

---

## Deployment topologies

### Single tenant (self-hosted, single team)

```
One Organization
├── Workspace: "default" (single workspace)
└── Users: 5-50
```

Simplest setup. Default workspace is auto-created. Single `HECATE_API_KEYS` env var.

### Multi-team SaaS

```
Organization: "Acme Corp"
├── Workspace: "engineering" (20 users, 5 admins)
├── Workspace: "marketing" (8 users, 2 admins)
├── Workspace: "finance" (3 users, 1 admin)
└── Workspace: "support" (12 users, 3 admins)
```

Per-workspace RBAC. Users can be members of multiple workspaces with different roles.

### Enterprise (multi-org, single Hecate)

```
Hecate instance
├── Org: "Acme Corp"
│   ├── Workspace: "engineering-prod"
│   └── Workspace: "engineering-staging"
├── Org: "Beta Inc"
│   ├── Workspace: "beta-default"
│   └── Workspace: "beta-experimental"
```

Organizations are completely isolated. Cross-org operations require explicit configuration (rare; usually each org gets its own Hecate).

---

## API key strategy

Hecate supports per-API-key configuration:

```python
class APIKeyModel(BaseModel):
    """API key with per-key scope and limits."""
    
    workspace_id: Mapped[uuid.UUID] = ...   # Which workspace the key acts in
    role: Mapped[WorkspaceRole] = ...       # Role granted by the key
    scopes: Mapped[list[str]] = ...         # Additional fine-grained scopes
    
    rate_limit_per_minute: Mapped[int | None] = ...
    expires_at: Mapped[datetime | None] = ...
    
    # Audit
    created_by: Mapped[uuid.UUID] = ...
    created_at: Mapped[datetime] = ...
    last_used_at: Mapped[datetime | None] = ...
```

A single workspace can have many API keys with different roles and rate limits — useful for:

- CI/CD key: `VIEWER` + 100 req/min
- Service integration key: `EDITOR` + 1000 req/min
- Backup key: `ADMIN` + only callable from specific IPs

---

## Security considerations

### Tenant isolation is enforced at three layers

1. **API layer**: every request validates `workspace_id` against the user's memberships
2. **Database layer**: queries filter by `workspace_id` (no raw access)
3. **Cache layer**: cache keys include `workspace_id` (no cross-tenant cache pollution)

This is **defense in depth**: even if one layer fails, the others prevent data leakage.

### What tenant isolation does NOT protect

- **Logs**: application logs may contain request data (PII redaction is best-effort)
- **Metrics**: aggregate metrics are per-tenant but can be correlated
- **Traces**: trace data may contain user IDs, request payloads — sensitive data must be redacted at source

For high-sensitivity tenants (healthcare, finance, government), consider:

- Per-tenant encryption keys (not yet supported — see )
- Per-tenant database encryption (transparent data encryption)
- Per-tenant SIEM routing

---

## What's NOT implemented (yet)

| Feature | Target |
|---|---|
| **Department hierarchy** (4-tier Org → Dept → Workspace) | Not planned — use external IAM |
| **Per-tenant encryption keys** | [1.x] |
| **Per-tenant SIEM routing** | [1.0] |
| **Tenant-level resource quotas** (currently workspace-level only) | [1.0] |
| **Outbound SCIM** (Hecate pushes to downstream systems) | [1.x] |

---

## Implementation references

- `src/hecate/models/organization.py` — OrganizationModel
- `src/hecate/models/workspace.py` — WorkspaceModel
- `src/hecate/models/workspace_member.py` — WorkspaceMemberModel + WorkspaceRole enum
- `src/hecate/auth/provider.py` — AuthProviderBase
- `src/hecate/auth/jwt_provider.py` — JWT auth
- `src/hecate/auth/api_key_provider.py` — API key auth
- `src/hecate/auth/oidc_provider.py` — OIDC SSO
- `src/hecate/auth/saml_provider.py` — SAML SSO
- `src/hecate/auth/ldap_provider.py` — LDAP auth
- `src/hecate/auth/sso_routes.py` — SSO callback routes
- `src/hecate/auth/resolver.py` — auth context resolution
- `src/hecate/auth/registration.py` — user registration flow
- `src/hecate/scim/` — SCIM 2.0 implementation
- `src/hecate/api/auth.py` — auth endpoints
- `src/hecate/api/middleware.py` — tenant isolation middleware

## Related documents

- [ADR-018: Zero Trust Identity Architecture](adr/018-zero-trust-identity-architecture.md) — the "why" behind this design
- [ADR-025: Enterprise Foundation Enhancement](adr/025-enterprise-foundation-enhancement.md) — recent enhancements
- [How-to: Configure SSO and SCIM](../how-to/configure-sso-scim.md) — operational recipe
- [Access Channel Design](access-channel-design.md) — auth at the gateway layer
- [Security Architecture](security-architecture.md) — audit + PII in security context
- [Reference: Data Models](../reference/data-models.md) — full tenant model reference
- Multi-Tenancy Architecture — current implementation