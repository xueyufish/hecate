# Memory API — Memory Management REST API

## Overview

Provides REST API endpoints for working memory block CRUD, user memory view/search, compression status query, and L4 knowledge memory management.

## Requirements

### REQ-1: Working Memory Block CRUD

- `GET /api/agents/{agent_id}/memory/blocks` — List all working memory blocks for an Agent
- `POST /api/agents/{agent_id}/memory/blocks` — Create or update a memory block (label + content)
- `PUT /api/agents/{agent_id}/memory/blocks/{block_id}` — Update a specific memory block
- `DELETE /api/agents/{agent_id}/memory/blocks/{block_id}` — Delete a memory block

### REQ-2: User Memory View and Search

- `GET /api/users/{user_id}/memories` — List all user memory facts (supports pagination)
- `GET /api/users/{user_id}/memories/search?q={query}` — Semantic search user memories
- `DELETE /api/users/{user_id}/memories/{memory_id}` — Delete a specific memory

### REQ-3: Compression Status Query

- `GET /api/sessions/{session_id}/compression` — Return session compression history (level, tokens saved, timestamp)

### REQ-4: Authentication & Authorization

- All endpoints SHALL require authentication via `get_auth_context()` dependency, replacing the previous `verify_api_key` dependency
- Agent memory blocks SHALL be accessible to users with `editor` or `admin` role in the agent's workspace
- User memories SHALL only be accessible to the user themselves
- workspace_id for memory operations SHALL be resolved from the authenticated workspace context (JWT claims or API key scope), not from request parameters
- System-scope API keys SHALL bypass workspace ownership checks

### REQ-5: L4 Knowledge Memory Endpoints

- `GET /api/agents/{agent_id}/knowledge` — List knowledge memories with optional `?tags=policy&limit=20&offset=0`
- `POST /api/agents/{agent_id}/knowledge/search` — Hybrid search with `{"query": "...", "top_k": 5, "tags": ["policy"], "mode": "hybrid"}`
- `POST /api/agents/{agent_id}/knowledge` — Create knowledge memory with `{"content": "...", "tags": [...], "importance": 0.8}`, stores in PostgreSQL + Qdrant
- `DELETE /api/agents/{agent_id}/knowledge/{memory_id}` — Soft-delete in PostgreSQL, remove from Qdrant

### REQ-6: Workspace Isolation

- All existing memory endpoints SHALL enforce workspace isolation via `workspace_id` resolved from the auth context, not from agent lookup or request parameter
- `workspace_id` SHALL be resolved automatically by the `get_auth_context()` dependency
- Service layer methods SHALL receive `workspace_id` from the auth context, not from direct parameters
- Queries SHALL include a `workspace_id` filter matching the authenticated workspace

## Scenarios

### Scenario 1: Manage Working Memory Blocks

```
Given User has Agent "assistant" (agent_id=abc)
When POST /api/agents/abc/memory/blocks {"label": "current_task", "content": "Write weekly report"}
Then Return 201 + created memory block details
And Agent can read this block in the next conversation turn
```

### Scenario 2: Search User Memories

```
Given User has a memory {fact: "likes Python", category: "preference"}
When GET /api/users/{user_id}/memories/search?q=programming language
Then Return result list containing that memory
```

### Scenario 3: View Compression History

```
Given Session has gone through 3 compressions
When GET /api/sessions/{session_id}/compression
Then Return compression record list [{level: "snip", tokens_saved: 1200, timestamp: "..."}, ...]
```

### Scenario 4: Manage Knowledge Memories

```
Given Agent "assistant" (agent_id=abc) has knowledge memory enabled
When POST /api/agents/abc/knowledge {"content": "Company policy: 2FA required for all accounts", "tags": ["policy", "security"], "importance": 0.9}
Then Return 201 + created knowledge memory details
And Agent can retrieve this knowledge via search
```

### Scenario 5: Search Knowledge Memories

```
Given Agent has 10 stored knowledge memories
When POST /api/agents/abc/knowledge/search {"query": "security policy", "top_k": 5}
Then Return scored search results from Qdrant hybrid search
```

### Scenario 6: Workspace Isolation on Memory Blocks

```
Given Agent "assistant" belongs to workspace "ws-1"
When GET /api/agents/abc/memory/blocks
Then Only blocks where workspace_id matches the agent's workspace are returned
```
