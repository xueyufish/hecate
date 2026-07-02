## MODIFIED Requirements

### REQ-1: Workspace Isolation on Memory Models
All memory models (L1 `MemoryBlockModel`, L3 `MemoryModel`, L4 `KnowledgeMemoryModel`) SHALL have a `workspace_id` UUID column as a first-class field. All service-layer queries SHALL filter by `workspace_id` to enforce tenant isolation.

#### Scenario: Query L1 memory blocks with workspace filter
- **WHEN** `WorkingMemoryService.list_blocks(agent_id, workspace_id=ws_id)` is called
- **THEN** Only return blocks where `workspace_id == ws_id` AND `agent_id == agent_id`

#### Scenario: Query L3 user memories with workspace filter
- **WHEN** `UserMemoryService.retrieve_memories(query, scope, workspace_id=ws_id)` is called
- **THEN** Only return memories where `workspace_id == ws_id`, in addition to any scope filters

#### Scenario: Query L4 knowledge with workspace filter
- **WHEN** `KnowledgeMemoryService.search(query, agent_id, workspace_id=ws_id)` is called
- **THEN** Qdrant search payload filter SHALL include `workspace_id == ws_id` as a mandatory filter condition

#### Scenario: Create memory block with workspace
- **WHEN** A new `MemoryBlockModel` is created
- **THEN** `workspace_id` is set from the agent's workspace (validated against auth context)

#### Scenario: Create user memory with workspace
- **WHEN** A new `MemoryModel` is created
- **THEN** `workspace_id` is set from the request auth context

#### Scenario: Vector store payload includes workspace_id
- **WHEN** a memory vector is stored in Qdrant for L4 knowledge memory
- **THEN** the point payload SHALL include `workspace_id` matching the memory's workspace, and search queries SHALL filter by it

### REQ-3: API Workspace Context
All memory API endpoints SHALL accept workspace context from the authentication middleware. The `workspace_id` SHALL be validated against the authenticated user's permitted workspaces.

#### Scenario: Memory block endpoints workspace enforcement
- **WHEN** `POST /api/agents/{agent_id}/memory-blocks` is called
- **THEN** The agent's `workspace_id` is used as the workspace context, and the created block inherits it

#### Scenario: User memory endpoints workspace enforcement
- **WHEN** `GET /api/users/{user_id}/memories` is called
- **THEN** Only memories within the authenticated workspace are returned

## ADDED Requirements

### Requirement: Vector store workspace payload on all insertions
All vector store adapters (Qdrant, Chroma) SHALL include `workspace_id` in the payload metadata when inserting points for memory or knowledge base chunks.

#### Scenario: Qdrant upsert includes workspace_id
- **WHEN** a vector point is upserted into Qdrant
- **THEN** the payload SHALL contain a `workspace_id` field with the workspace UUID

#### Scenario: Chroma upsert includes workspace_id
- **WHEN** a vector point is added to Chroma
- **THEN** the metadata SHALL contain a `workspace_id` field with the workspace UUID
