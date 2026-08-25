# Multi-Tenancy

Hecate is designed for organizations that run multiple teams, projects, or customers on a single deployment. The tenancy model is a three-level hierarchy — **Organization → Workspace → User** — with data-level isolation enforced by a `workspace_id` foreign key on every data model that holds business content.

This page explains the hierarchy, how isolation works, and what it means in practice when you configure agents and manage users.

---

## The three levels

```
Organization
    │
    ├── Workspace A ──┬── Agents
    │                 ├── Workflows
    │                 ├── Knowledge Bases
    │                 ├── Tools
    │                 ├── Skills
    │                 └── Prompts
    │
    ├── Workspace B ──┬── Agents
    │                 └── ... (isolated from A)
    │
    └── User ──── Role (admin / editor / viewer)
```

### Organization

The top-level tenant boundary. An Organization owns Users and Workspaces. In a SaaS deployment, each customer is one Organization. In a self-hosted internal deployment, the Organization might represent the company itself, with Workspaces per team or product.

Organizations carry settings (JSONB) for organization-wide configuration.

### Workspace

The **unit of isolation**. All business content — Agents, Workflows, Knowledge Bases, Tools, Skills, Prompts — belongs to a Workspace. A user working in Workspace A cannot see, query, or invoke resources in Workspace B through the normal API. This isolation is enforced at the data layer (see below), not just at the UI layer.

Workspaces are where teams collaborate. A common pattern is one Workspace per project, per product line, or per internal team.

### User

An authenticated actor within an Organization. Users are assigned a **role** that governs what they can do across Workspaces:

| Role | Capabilities |
|------|-------------|
| **admin** | Create and delete Workspaces, manage users, configure organization settings |
| **editor** | Create and modify agents, workflows, knowledge bases, and tools within Workspaces they can access |
| **viewer** | Read-only access to resources within accessible Workspaces |

Workspace membership is managed separately from Organization-level roles — a user may be a member of multiple Workspaces with different access levels in each.

---

## How isolation works

Tenant isolation is enforced by a `workspace_id` foreign key on every data model that holds business content. When a query runs — whether from the API, the engine, or a service — it is scoped to the `workspace_id` of the authenticated request. A query for agents in Workspace A literally cannot return rows from Workspace B because the database-level filter excludes them.

This approach is called **data-level isolation**. It has two practical advantages over the alternatives:

| Approach | How it isolates | Tradeoff |
|----------|----------------|----------|
| **Database-per-tenant** | Each tenant gets a separate database | Strong isolation, but operationally expensive — migrations, backups, and connection pools multiply by tenant count |
| **Schema-per-tenant** | Each tenant gets a separate schema in a shared database | Better than database-per-tenant, still requires per-tenant DDL management |
| **Data-level (Hecate)** | Shared tables, `workspace_id` column on every row | Single schema to manage, scales to many tenants, isolation enforced in the query layer |

Hecate uses data-level isolation because it scales to hundreds of Workspaces without operational overhead, while still providing the guarantees that matter: no cross-tenant data leakage through the application layer.

The `workspace_id` column is currently present on many data models — including agents, workflows, knowledge bases, documents, tools, sessions, checkpoints, audit logs, budgets, alerts, and every other resource that holds tenant-scoped business content. The count grows as new workspace-scoped resources are added; queries are constructed from `workspace_id` joins on the authenticated request, so the database itself filters rows by tenant.

---

## What lives where

Resources are scoped at different levels of the hierarchy:

| Scope | Resources | Notes |
|-------|-----------|-------|
| **Organization** | Users, Workspaces, Organization settings | Shared across the Organization |
| **Workspace** | Agents, Workflows, Knowledge Bases, Tools, Skills, Prompts, Documents, Sessions | Isolated per Workspace — the bulk of business content |
| **Agent** | Memory Blocks, bound Tools, bound Knowledge Bases, bound Workflow | Owned by a single Agent within a Workspace |
| **Cross-Workspace** | Skills (system-level), LLM provider configs, global tool definitions | Shared infrastructure, not tenant-scoped |

This scoping means a team working in Workspace A can build and iterate on their agents independently, while sharing the same underlying LLM providers and platform configuration as other Workspaces.

---

## Authentication and identity

Users authenticate at the Organization level via one of:

- **API Key** (`hcat_*`) — application identity for server-to-server integration
- **JWT (Bearer)** — end-user identity for interactive sessions
- **SSO** — OIDC, SAML, or LDAP via the [SSO configuration guide](../how-to/configure-sso-scim.md)
- **SCIM v2** — automated user and group provisioning from an IdP

After authentication, every request carries the user's identity and the target Workspace. The engine, services, and API all scope their operations to that Workspace. For details on the identity model and the planned two-tier (app-level + user-level) token architecture, see the [security architecture](../design/security-architecture.md).

---

## Practical implications

| You want to... | What this means |
|----------------|----------------|
| Onboard a new team | Create a Workspace, add the team's users as members |
| Run two projects with no data overlap | Use two Workspaces — isolation is automatic |
| Share an agent across teams | Copy the agent definition between Workspaces, or restructure into a shared Workspace |
| Enforce per-team LLM budgets | Budgets are tracked per Workspace and per Agent |
| Audit who did what | Every action is logged with user, Workspace, and agent context — see [Guardrails and Hooks](guardrails.md) |

---

## Further reading

- [Core Concepts: Multi-Tenancy](../design/concepts.md) — entity relationships and the full data model
- [Enterprise Foundation Design](../design/enterprise-foundation-design.md) — multi-tenancy, security, and deployment infrastructure
- [Configure SSO and SCIM](../how-to/configure-sso-scim.md) — wiring identity providers for production
- [Security Architecture](../design/security-architecture.md) — authentication, RBAC, and the audit trail
- [ADR-018: Zero Trust Identity Architecture](../design/adr/018-zero-trust-identity-architecture.md) — the planned two-tier identity model
