## ADDED Requirements

### Requirement: JWT claims enrichment
The system SHALL include `org_id`, `workspace_id`, and `role` claims in access tokens, in addition to the existing `sub`, `type`, `exp`, and `iat` claims.

#### Scenario: Login returns enriched token
- **WHEN** a user authenticates via POST `/auth/login` with valid credentials
- **THEN** the system returns an access token with claims `{sub: user_id, type: "access", org_id: "...", workspace_id: "...", role: "editor", exp: ..., iat: ...}`. The `workspace_id` and `role` correspond to the user's most recently used workspace, or the first workspace they have access to.

#### Scenario: Login with no workspace membership
- **WHEN** a user authenticates but has no workspace membership
- **THEN** the system returns an access token with `org_id: null`, `workspace_id: null`, `role: null`

### Requirement: Auth context dependency
The system SHALL provide a `get_auth_context()` FastAPI dependency that resolves the authenticated request to an `AuthContext` dataclass containing: `user_id`, `org_id`, `workspace_id`, `role`, `auth_method` ("jwt" or "api_key"), `api_key_scope` (nullable).

#### Scenario: JWT auth context
- **WHEN** a request authenticates via JWT bearer token
- **THEN** `get_auth_context()` returns `AuthContext(user_id=..., org_id=..., workspace_id=..., role=..., auth_method="jwt", api_key_scope=None)`

#### Scenario: Workspace API key auth context
- **WHEN** a request authenticates via a workspace-scope API key
- **THEN** `get_auth_context()` returns `AuthContext(user_id=created_by, org_id=..., workspace_id=..., role="admin", auth_method="api_key", api_key_scope="workspace")`

#### Scenario: System API key auth context
- **WHEN** a request authenticates via a system-scope API key
- **THEN** `get_auth_context()` returns `AuthContext(user_id=created_by, org_id=None, workspace_id=None, role=None, auth_method="api_key", api_key_scope="system")`

### Requirement: Workspace switching
The system SHALL provide an endpoint for switching the active workspace context. This issues new access and refresh tokens with the target workspace's claims.

#### Scenario: Switch workspace
- **WHEN** an authenticated user sends POST `/auth/switch-workspace` with `{workspace_id: "..."}`
- **THEN** the system verifies the user is a member of the target workspace, issues new tokens with updated `org_id`, `workspace_id`, and `role` claims, and returns `{access_token, refresh_token, token_type}`

#### Scenario: Switch to inaccessible workspace
- **WHEN** an authenticated user sends POST `/auth/switch-workspace` with a workspace they are not a member of
- **THEN** the system returns `403 Forbidden`

### Requirement: Login response includes accessible workspaces
The login response SHALL include a list of workspaces the user has access to, allowing the client to present a workspace selector.

#### Scenario: Login returns workspace list
- **WHEN** a user logs in successfully
- **THEN** the response includes `{access_token, refresh_token, token_type, workspaces: [{id, name, slug, org_id, role}, ...]}`

### Requirement: SSO extension point
The `UserModel` SHALL include an optional `sso_id` field (nullable string) for storing the external identity provider's user identifier. This field is not used by the local auth flow but is reserved for future SSO integration (OIDC/SAML).

#### Scenario: User created with sso_id
- **WHEN** a user is created via SSO sync (future)
- **THEN** the `sso_id` field stores the external identity provider's user ID

#### Scenario: Local user has null sso_id
- **WHEN** a user registers via email/password
- **THEN** the `sso_id` field is null

### Requirement: Token refresh preserves workspace context
When refreshing tokens, the system SHALL preserve the workspace context from the refresh token or re-derive it from the user's current membership.

#### Scenario: Refresh preserves workspace
- **WHEN** a user refreshes tokens while having workspace context in the access token
- **THEN** the new tokens maintain the same `org_id`, `workspace_id`, and `role` claims
