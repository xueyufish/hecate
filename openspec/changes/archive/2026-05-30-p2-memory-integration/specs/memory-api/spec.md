# Memory API — Memory Management REST API

## Overview

Provides REST API endpoints for working memory block CRUD, user memory view/search, and compression status query.

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

- All endpoints require API Key authentication (reuse `verify_api_key` dependency)
- Agent memory blocks are only accessible to the Agent's owner
- User memories are only accessible to the user themselves

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
