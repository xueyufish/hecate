## MODIFIED Requirements

### Requirement: L4 Knowledge Memory Endpoints (ADDED)

The system SHALL provide REST API endpoints for L4 knowledge memory management under `/api/agents/{agent_id}/knowledge`.

#### Scenario: List knowledge memories
- **WHEN** `GET /api/agents/{agent_id}/knowledge` is called with optional `?tags=policy&limit=20&offset=0`
- **THEN** Return paginated list of knowledge memories for the agent within the authenticated workspace

#### Scenario: Search knowledge memories
- **WHEN** `POST /api/agents/{agent_id}/knowledge/search` with `{"query": "...", "top_k": 5, "tags": ["policy"], "mode": "hybrid"}`
- **THEN** Return scored search results from Qdrant hybrid search

#### Scenario: Create knowledge memory
- **WHEN** `POST /api/agents/{agent_id}/knowledge` with `{"content": "...", "tags": [...], "importance": 0.8}`
- **THEN** Create knowledge memory with embedding, store in PostgreSQL + Qdrant, return 201

#### Scenario: Delete knowledge memory
- **WHEN** `DELETE /api/agents/{agent_id}/knowledge/{memory_id}` is called
- **THEN** Soft-delete in PostgreSQL, remove from Qdrant, return 204

### Requirement: Workspace Context on Existing Endpoints (MODIFIED)

All existing memory API endpoints SHALL now enforce workspace isolation. The `workspace_id` is resolved from the agent's workspace (for L1 endpoints) or from auth context (for L3 endpoints).

#### Scenario: List memory blocks with workspace filter
- **WHEN** `GET /api/agents/{agent_id}/memory-blocks` is called
- **THEN** Only return blocks where `workspace_id` matches the agent's workspace

#### Scenario: List user memories with workspace filter
- **WHEN** `GET /api/users/{user_id}/memories` is called
- **THEN** Only return memories within the authenticated workspace

#### Scenario: Search user memories with workspace filter
- **WHEN** `GET /api/users/{user_id}/memories/search?q={query}` is called
- **THEN** Only search memories within the authenticated workspace
