## ADDED Requirements

### Requirement: Workspace Isolation on Memory Models

All memory models (L1 `MemoryBlockModel`, L3 `MemoryModel`, L4 `KnowledgeMemoryModel`) SHALL have a `workspace_id` UUID column as a first-class field. All service-layer queries SHALL filter by `workspace_id` to enforce tenant isolation.

#### Scenario: Query L1 memory blocks with workspace filter
- **WHEN** `WorkingMemoryService.list_blocks(agent_id, workspace_id=ws_id)` is called
- **THEN** Only return blocks where `workspace_id == ws_id` AND `agent_id == agent_id`

#### Scenario: Query L3 user memories with workspace filter
- **WHEN** `UserMemoryService.retrieve_memories(query, scope, workspace_id=ws_id)` is called
- **THEN** Only return memories where `workspace_id == ws_id`, in addition to any scope filters

#### Scenario: Query L4 knowledge with workspace filter
- **WHEN** `KnowledgeMemoryService.search(query, agent_id, workspace_id=ws_id)` is called
- **THEN** Qdrant search payload filter includes `workspace_id == ws_id`

#### Scenario: Create memory block with workspace
- **WHEN** A new `MemoryBlockModel` is created
- **THEN** `workspace_id` is set from the agent's workspace (validated against auth context)

#### Scenario: Create user memory with workspace
- **WHEN** A new `MemoryModel` is created
- **THEN** `workspace_id` is set from the request auth context

### Requirement: Alembic Migration for workspace_id

An Alembic migration SHALL add `workspace_id` column to `memory_blocks` and `memories` tables with server default `UUID('00000000-0000-0000-0000-000000000000')` and create composite indexes `(workspace_id, deleted)`.

#### Scenario: Migration adds workspace_id to memory_blocks
- **WHEN** `alembic upgrade head` is run
- **THEN** `memory_blocks` table has new `workspace_id` UUID column with index `idx_memory_blocks_workspace`

#### Scenario: Migration adds workspace_id to memories
- **WHEN** `alembic upgrade head` is run
- **THEN** `memories` table has new `workspace_id` UUID column with index `idx_memories_workspace`

#### Scenario: Existing rows get default workspace
- **WHEN** Migration runs on an existing database
- **THEN** All existing rows in `memory_blocks` and `memories` have `workspace_id` set to the zero UUID

### Requirement: API Workspace Context

All memory API endpoints SHALL accept workspace context from the authentication middleware. The `workspace_id` SHALL be validated against the authenticated user's permitted workspaces.

#### Scenario: Memory block endpoints workspace enforcement
- **WHEN** `POST /api/agents/{agent_id}/memory-blocks` is called
- **THEN** The agent's `workspace_id` is used as the workspace context, and the created block inherits it

#### Scenario: User memory endpoints workspace enforcement
- **WHEN** `GET /api/users/{user_id}/memories` is called
- **THEN** Only memories within the authenticated workspace are returned
