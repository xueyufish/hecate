# skill-api Delta

## ADDED Requirements

### Requirement: Promote discovered skill to agent binding

The system SHALL provide a promote endpoint that converts a discovery-sourced skill into an explicit agent binding: `POST /api/agents/{id}/skills/promote` with the skill name. The endpoint SHALL validate that the named skill resolves in the caller's workspace or the bundled origin (provider-registry precedence applied), is not deleted, has `model_invocable=true`, and — for plugin-sourced skills — that its owning plugin is enabled; on validation failure it SHALL return 404 with an informative error and SHALL NOT modify the agent. On success it SHALL append the skill name to the agent's `skills` list (idempotent: already-bound names return the unchanged list), and the response SHALL include the updated `skills` list plus a `frozen: false` marker and a `next_step` hint directing the caller to commit an agent version to freeze the skill's content into the version reference manifest. The existing add/remove association endpoints SHALL remain unchanged (blind name append/remove without resolution validation).

#### Scenario: Promote appends resolvable skill to binding
- **WHEN** `POST /api/agents/{id}/skills/promote` is called with a skill name that resolves in the workspace and is not already in the agent's `skills` list
- **THEN** the skill name SHALL be appended to the list and the response SHALL contain the updated `skills`, `frozen: false`, and the commit-version `next_step` hint

#### Scenario: Promote is idempotent for already-bound skills
- **WHEN** the named skill is already in the agent's `skills` list
- **THEN** the response SHALL return the unchanged skills list with the same markers and no duplicate entry

#### Scenario: Promote rejects unresolvable name
- **WHEN** the named skill does not exist in the workspace or bundled origin
- **THEN** the endpoint SHALL return 404 and the agent's `skills` list SHALL remain unchanged

#### Scenario: Promote rejects model-invisible skill
- **WHEN** the named skill resolves but `model_invocable=false`
- **THEN** the endpoint SHALL return 404 with an error explaining the skill cannot be model-invoked, and the list SHALL remain unchanged
