## ADDED Requirements

### Requirement: Workspace membership model
The system SHALL maintain a `WorkspaceMemberModel` linking users to workspaces with a role. Each user-workspace pair SHALL be unique. The role SHALL be one of: `admin`, `editor`, `viewer`.

#### Scenario: User added to workspace
- **WHEN** a workspace admin sends POST `/api/orgs/{org_id}/workspaces/{ws_id}/members` with `{user_id: "...", role: "editor"}`
- **THEN** the system creates a WorkspaceMemberModel entry and returns `201`

#### Scenario: Duplicate membership rejected
- **WHEN** a workspace admin tries to add a user who is already a member of the workspace
- **THEN** the system returns `409 Conflict`

#### Scenario: Remove member from workspace
- **WHEN** a workspace admin sends DELETE `/api/orgs/{org_id}/workspaces/{ws_id}/members/{user_id}`
- **THEN** the system removes the membership entry and returns `204`

### Requirement: Role-based permission enforcement
The system SHALL enforce workspace-level permissions based on the user's role. Permission checks SHALL be implemented as FastAPI dependency functions that can be applied to any endpoint.

#### Scenario: Admin manages workspace settings
- **WHEN** a user with `admin` role sends PATCH `/api/orgs/{org_id}/workspaces/{ws_id}` with settings update
- **THEN** the system applies the update and returns `200`

#### Scenario: Editor cannot manage members
- **WHEN** a user with `editor` role sends POST `/api/orgs/{org_id}/workspaces/{ws_id}/members`
- **THEN** the system returns `403 Forbidden`

#### Scenario: Viewer cannot create resources
- **WHEN** a user with `viewer` role sends POST `/api/agents` with a new agent definition
- **THEN** the system returns `403 Forbidden`

#### Scenario: Viewer can read resources
- **WHEN** a user with `viewer` role sends GET `/api/agents`
- **THEN** the system returns the list of agents in the authenticated workspace

#### Scenario: Editor can create and edit resources
- **WHEN** a user with `editor` role sends POST `/api/agents` or PATCH `/api/agents/{id}`
- **THEN** the system creates or updates the agent in the authenticated workspace

### Requirement: Permission dependency functions
The system SHALL provide the following FastAPI dependency functions for use in API endpoints: `require_workspace_admin()`, `require_workspace_editor()`, `require_workspace_viewer()`. These dependencies SHALL resolve the authenticated user's role in the current workspace and raise `403 Forbidden` if the role is insufficient.

#### Scenario: require_workspace_admin dependency
- **WHEN** an endpoint uses `Depends(require_workspace_admin)` and the authenticated user has `viewer` role
- **THEN** the dependency raises `403 Forbidden` before the endpoint logic executes

#### Scenario: require_workspace_editor dependency allows admin
- **WHEN** an endpoint uses `Depends(require_workspace_editor)` and the authenticated user has `admin` role
- **THEN** the dependency passes — admin implicitly satisfies editor-level requirements

#### Scenario: require_workspace_viewer dependency allows all roles
- **WHEN** an endpoint uses `Depends(require_workspace_viewer)` and the authenticated user has any role (admin/editor/viewer)
- **THEN** the dependency passes

### Requirement: Role assignment and modification
A workspace admin SHALL be able to change the role of existing members. The admin role cannot be removed from the last admin in a workspace — there must always be at least one admin.

#### Scenario: Change member role
- **WHEN** a workspace admin sends PATCH `/api/orgs/{org_id}/workspaces/{ws_id}/members/{user_id}` with `{role: "viewer"}`
- **THEN** the system updates the member's role from editor to viewer

#### Scenario: Cannot remove last admin
- **WHEN** a workspace admin attempts to change their own role to `editor` and they are the only admin in the workspace
- **THEN** the system returns `409 Conflict` with message "Workspace must have at least one admin"

### Requirement: Workspace creator is admin
When a workspace is created, the creator SHALL automatically be added as a workspace member with `admin` role. This membership is mandatory and cannot be removed while the workspace exists.

#### Scenario: Creator gets admin role
- **WHEN** an org owner creates a new workspace
- **THEN** the system creates a WorkspaceMemberModel entry for the creator with `role: "admin"`

### Requirement: System-scope API key bypasses workspace RBAC
A system-scope API key SHALL bypass workspace-level RBAC checks. It can access any resource in any workspace without membership requirements.

#### Scenario: System key accesses any workspace
- **WHEN** a request authenticates with a system-scope API key
- **THEN** RBAC dependencies pass regardless of workspace membership or role

#### Scenario: Workspace key respects RBAC
- **WHEN** a request authenticates with a workspace-scope API key
- **THEN** the key is treated as having `admin` role within its bound workspace, and `403 Forbidden` for any other workspace
