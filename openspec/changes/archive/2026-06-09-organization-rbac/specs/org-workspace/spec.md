## ADDED Requirements

### Requirement: Organization CRUD
The system SHALL provide REST API endpoints for creating, reading, updating, and soft-deleting organizations. Each organization SHALL have a unique `slug` used for human-readable addressing. The `slug` SHALL be auto-generated from the organization name if not provided, and SHALL be immutable after creation.

#### Scenario: Create organization
- **WHEN** an authenticated user sends POST `/api/orgs` with `{name: "Acme Corp", slug: "acme"}`
- **THEN** the system creates an OrganizationModel with a UUID `id`, sets `owner_id` to the requesting user, and returns `201` with `{id, name, slug, owner_id, created_at}`

#### Scenario: Create organization with auto-slug
- **WHEN** an authenticated user sends POST `/api/orgs` with `{name: "Acme Corp"}` (no slug)
- **THEN** the system auto-generates `slug: "acme-corp"` from the name and returns `201`

#### Scenario: Duplicate slug rejected
- **WHEN** an authenticated user sends POST `/api/orgs` with a `slug` that already exists
- **THEN** the system returns `409 Conflict` with error detail

#### Scenario: List organizations
- **WHEN** an authenticated user sends GET `/api/orgs`
- **THEN** the system returns paginated list of organizations where the user is the owner

#### Scenario: Get organization by ID
- **WHEN** an authenticated user sends GET `/api/orgs/{org_id}`
- **THEN** the system returns the organization details if the user is the owner

#### Scenario: Update organization
- **WHEN** an org owner sends PATCH `/api/orgs/{org_id}` with `{name: "Acme Inc"}`
- **THEN** the system updates the organization name and returns `200` with updated data

#### Scenario: Delete organization
- **WHEN** an org owner sends DELETE `/api/orgs/{org_id}`
- **THEN** the system soft-deletes the organization and all associated workspaces, provided no active resources exist in workspaces

### Requirement: Organization ownership
Each organization SHALL have exactly one `owner_id` (FK to UserModel). The owner is the initial admin of all workspaces under the org. Ownership can be transferred to another user who is a member of the organization.

#### Scenario: Owner is set at creation
- **WHEN** a user creates an organization
- **THEN** the `owner_id` is set to the creating user's ID

#### Scenario: Transfer ownership
- **WHEN** the current owner sends POST `/api/orgs/{org_id}/transfer-ownership` with `{new_owner_id: "..."}`
- **THEN** the system verifies the new owner is a member of the org, updates `owner_id`, and ensures the new owner has admin role in all org workspaces

### Requirement: Workspace CRUD
The system SHALL provide REST API endpoints for creating, reading, updating, and soft-deleting workspaces within an organization. Each workspace SHALL have a unique `slug` scoped to its parent organization (not globally unique). The workspace is the resource isolation boundary — all tenant-scoped resources belong to exactly one workspace.

#### Scenario: Create workspace in organization
- **WHEN** an org owner sends POST `/api/orgs/{org_id}/workspaces` with `{name: "Production", slug: "prod"}`
- **THEN** the system creates a WorkspaceModel with `org_id` FK, sets `slug: "prod"`, adds the creator as workspace admin in WorkspaceMemberModel, and returns `201`

#### Scenario: Workspace slug scoped to org
- **WHEN** an org owner creates a workspace with `slug: "default"` in org A, and another org also has a workspace with `slug: "default"`
- **THEN** both operations succeed — slug uniqueness is per-organization, not global

#### Scenario: List workspaces in organization
- **WHEN** an org member sends GET `/api/orgs/{org_id}/workspaces`
- **THEN** the system returns paginated list of workspaces in the organization where the user is a member

#### Scenario: Update workspace
- **WHEN** a workspace admin sends PATCH `/api/orgs/{org_id}/workspaces/{workspace_id}` with `{name: "Staging"}`
- **THEN** the system updates the workspace name and returns `200`

#### Scenario: Delete workspace with resources
- **WHEN** a workspace admin sends DELETE `/api/orgs/{org_id}/workspaces/{workspace_id}` and the workspace contains active (non-deleted) resources
- **THEN** the system returns `409 Conflict` with error detail listing the resource types that must be deleted first

#### Scenario: Delete empty workspace
- **WHEN** a workspace admin sends DELETE and the workspace has no active resources
- **THEN** the system soft-deletes the workspace and its membership records

### Requirement: Default organization and workspace bootstrap
The system SHALL create a default organization (`id: 00000000-0000-0000-0000-000000000000`, `slug: "default"`) and default workspace (`id: 00000000-0000-0000-0000-000000000000`, `slug: "default"`, `org_id: 00000000-...`) during the initial database migration. This ensures backward compatibility with existing single-tenant deployments.

#### Scenario: Fresh installation bootstrap
- **WHEN** the database migration runs on a fresh database
- **THEN** the system creates default org and default workspace with zero-UUID IDs

#### Scenario: Upgrade from existing deployment
- **WHEN** the database migration runs on an existing database with resources having zero-UUID workspace_id
- **THEN** existing resources are automatically part of the default workspace because their workspace_id matches the default workspace ID

### Requirement: Workspace FK enforcement
All models that currently have a `workspace_id` column (AgentModel, WorkflowModel, SkillModel, ToolModel, KnowledgeBaseModel, PromptModel, MemoryBlockModel, MemoryModel, KnowledgeMemoryModel) SHALL have a FK constraint referencing WorkspaceModel.id. The FK SHALL be non-nullable with a default of the zero-UUID default workspace.

#### Scenario: Create resource with valid workspace_id
- **WHEN** a user creates an agent with `workspace_id` referencing an existing workspace
- **THEN** the operation succeeds and the agent is scoped to that workspace

#### Scenario: Create resource with invalid workspace_id
- **WHEN** a user creates an agent with `workspace_id` that does not reference any workspace
- **THEN** the database raises a FK violation and the API returns `400 Bad Request`

### Requirement: Workspace-scoped resource listing
All API endpoints that list tenant-scoped resources (agents, workflows, skills, tools, knowledge bases, prompts, memory blocks, memories, knowledge memories) SHALL filter results by the authenticated workspace context.

#### Scenario: List agents in workspace
- **WHEN** a user with workspace context sends GET `/api/agents`
- **THEN** the system returns only agents where `workspace_id` matches the authenticated workspace

#### Scenario: Cross-workspace isolation
- **WHEN** user A is a member of workspace W1 but not workspace W2
- **THEN** listing agents with workspace W1 context returns W1 agents only; attempting to access W2 resources returns `403 Forbidden`
