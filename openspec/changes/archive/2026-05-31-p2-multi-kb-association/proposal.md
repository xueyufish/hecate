## Why

Agents already store `knowledge_base_ids` as a JSON list in the database, the RAG pipeline already iterates over multiple KB IDs, and the agent configurator UI already provides a multi-select component. However, the system lacks **end-to-end integration**: when chatting with an agent, the frontend does not pass the agent's associated KB IDs to the chat endpoint, there is no validation that referenced KB IDs actually exist, and deleting a KB leaves stale references in agent records. This change closes these gaps to make multi-KB association a fully functional, production-ready feature.

## What Changes

- Add KB ID validation when creating or updating agents — reject non-existent or soft-deleted KB IDs with a clear 400 error
- Add cascade cleanup — when a knowledge base is soft-deleted, automatically remove its ID from all agents' `knowledge_base_ids` arrays
- Auto-load agent's KB IDs in the chat flow — when the frontend chats with an agent, fetch and pass the agent's `knowledge_base_ids` to the `/v1/chat/completions` endpoint
- Show active KB indicators in the chat UI — display which knowledge bases are being used for context during a conversation
- Add a reverse-lookup API endpoint — `GET /api/knowledge-bases/{id}/agents` to find which agents use a specific KB
- Improve cross-KB search result ranking — aggregate results across all KBs with global score ranking instead of per-KB top-N then merge

## Capabilities

### New Capabilities
- `multi-kb-association`: End-to-end multi-KB support for agents — validation, cascade cleanup, auto-loading in chat, reverse lookup, and cross-KB result ranking

### Modified Capabilities
- `citation-display`: Requirement change — citations SHALL be generated automatically when chatting via an agent with associated KBs (not only when `kb_ids` is explicitly passed)
- `agent-configurator`: Requirement change — agent configurator SHALL display KB validation errors and show KB status (active/deleted) in the selector

## Impact

- **Backend models**: `AgentModel` (no schema change, `knowledge_base_ids` JSON column retained)
- **Backend API**: `agents.py` (validation in create/update), `knowledge.py` (cascade cleanup on delete, new reverse-lookup endpoint), `chat.py` (no change — already accepts `kb_ids`)
- **Backend services**: `conversation.py` (no change — already iterates multiple KBs), new validation helper in agents service
- **Frontend**: Chat page (`chat/[conversationId]/page.tsx`) — load agent's KB IDs and pass to chat endpoint; display active KB badges
- **Frontend**: Agent configurator — show KB validation errors
- **Tests**: Agent CRUD validation tests, KB cascade cleanup tests, chat integration tests with auto-loaded KB IDs
- **No Alembic migration needed** — the existing JSON column is sufficient for the M:N relationship
