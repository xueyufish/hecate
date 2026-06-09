## MODIFIED Requirements

### Requirement: Authentication & Authorization

- All endpoints SHALL require authentication via `get_auth_context()` dependency, replacing the previous `verify_api_key` dependency
- Agent memory blocks SHALL be accessible to users with `editor` or `admin` role in the agent's workspace
- User memories SHALL only be accessible to the user themselves
- workspace_id for memory operations SHALL be resolved from the authenticated workspace context (JWT claims or API key scope), not from request parameters
- System-scope API keys SHALL bypass workspace ownership checks

#### Scenario: Workspace-scoped memory access
- **WHEN** a user with `editor` role in workspace W1 sends GET `/api/agents/{agent_id}/memory/blocks` where the agent belongs to workspace W1
- **THEN** the system returns the memory blocks

#### Scenario: Cross-workspace memory access denied
- **WHEN** a user with `editor` role in workspace W1 tries to access memory blocks of an agent in workspace W2
- **THEN** the system returns `403 Forbidden`

#### Scenario: Workspace context from auth
- **WHEN** a memory endpoint is called with workspace context from JWT or API key
- **THEN** the system uses the authenticated workspace_id for all queries, ignoring any workspace_id in the request body

### Requirement: Workspace Isolation

- All existing memory endpoints SHALL enforce workspace isolation via `workspace_id` resolved from the auth context, not from agent lookup or request parameter
- `workspace_id` SHALL be resolved automatically by the `get_auth_context()` dependency
- Service layer methods SHALL receive `workspace_id` from the auth context, not from direct parameters
- Queries SHALL include a `workspace_id` filter matching the authenticated workspace

#### Scenario: Memory query uses auth workspace
- **WHEN** a user accesses memory endpoints with workspace W1 in auth context
- **THEN** all queries filter by `workspace_id = W1.id` regardless of any workspace_id in request body
