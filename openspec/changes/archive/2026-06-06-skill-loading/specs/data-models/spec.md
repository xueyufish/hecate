## MODIFIED Requirements

### Requirement: SkillModel supports multi-tenant workspace isolation
The `SkillModel` SHALL include a `workspace_id` column of type UUID, defaulting to the zero UUID `00000000-0000-0000-0000-000000000000`. The unique index SHALL be `(workspace_id, name)` instead of `(name)` alone, allowing different workspaces to have skills with the same name.

#### Scenario: System skill with zero UUID
- **WHEN** a skill is created with `source="system"`
- **THEN** `workspace_id` SHALL default to `00000000-0000-0000-0000-000000000000`

#### Scenario: User skill with workspace ID
- **WHEN** a skill is created with `source="user"` by a user in workspace A
- **THEN** `workspace_id` SHALL be set to workspace A's UUID

#### Scenario: Same skill name in different workspaces
- **WHEN** workspace A has a skill named "helper" and workspace B creates a skill named "helper"
- **THEN** both skills SHALL coexist without unique constraint violation

#### Scenario: SkillCreateSchema includes workspace_id
- **WHEN** a skill is created via API
- **THEN** `workspace_id` SHALL be automatically set from the authenticated user's workspace context, not from the request body
