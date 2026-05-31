## 1. Backend: KB Validation

- [x] 1.1 Add `validate_knowledge_base_ids()` helper in `src/hecate/api/management/agents.py` that queries `KnowledgeBaseModel` to verify all KB IDs exist and are not soft-deleted, raising HTTP 400 with invalid IDs listed
- [x] 1.2 Call `validate_knowledge_base_ids()` in `create_agent()` endpoint before creating the agent model
- [x] 1.3 Call `validate_knowledge_base_ids()` in `update_agent()` endpoint when `knowledge_base_ids` is provided in the update schema
- [x] 1.4 Add tests in `tests/test_api/test_agents.py` (or new file `tests/test_api/test_agent_kb_validation.py`) for: valid KB IDs, non-existent KB IDs, soft-deleted KB IDs, empty list, no KB IDs field

## 2. Backend: Cascade Cleanup

- [x] 2.1 Add `cleanup_kb_references()` helper function in `src/hecate/api/management/knowledge.py` that removes the deleted KB ID from all agents' `knowledge_base_ids` JSON arrays
- [x] 2.2 Call `cleanup_kb_references()` in the KB delete endpoint after soft-deleting the KB, before returning the response
- [x] 2.3 Add tests for cascade cleanup: delete KB referenced by multiple agents, delete KB not referenced by any agent, delete KB referenced by one agent with other KBs

## 3. Backend: Reverse Lookup API

- [x] 3.1 Add `GET /api/knowledge-bases/{id}/agents` endpoint in `src/hecate/api/management/knowledge.py` that queries agents containing the KB ID in their `knowledge_base_ids` JSON array, with pagination support
- [x] 3.2 Return 404 if the KB does not exist or is deleted
- [x] 3.3 Add tests for reverse lookup: agents using a KB, non-existent KB, pagination

## 4. Backend: Cross-KB Search Aggregation

- [x] 4.1 Refactor `_retrieve_knowledge()` in `src/hecate/services/conversation.py` to use `asyncio.gather()` for parallel KB searches instead of sequential iteration
- [x] 4.2 Refactor `knowledge_query()` in `src/hecate/services/orchestration/agent_execution_port.py` to use `asyncio.gather()` for parallel KB searches
- [x] 4.3 Add tests verifying parallel search across multiple KBs and global score ranking

## 5. Frontend: Chat Auto-Load KB IDs

- [x] 5.1 Update `web/src/app/(dashboard)/chat/[conversationId]/page.tsx` to fetch agent's `knowledge_base_ids` from agent config (already fetched for model name) and store in state
- [x] 5.2 Pass `kb_ids` in the `/v1/chat/completions` request when sending messages, populated from the agent's `knowledge_base_ids`
- [x] 5.3 Fetch KB names for display by calling `GET /api/knowledge-bases` or individual KB lookups when agent has associated KB IDs

## 6. Frontend: KB Indicators in Chat UI

- [x] 6.1 Add KB indicator badges in the chat page header showing active KB names for the current conversation
- [x] 6.2 Display nothing when agent has no KB associations
- [x] 6.3 Show loading state while KB names are being fetched

## 7. Frontend: Agent Configurator Error Handling

- [x] 7.1 Update `web/src/components/agent/agent-configurator.tsx` to catch and display 400 errors from invalid `knowledge_base_ids` near the KB selector
- [x] 7.2 Add error state display to `web/src/components/agent/knowledge-selector.tsx` showing validation error message

## 8. Verification

- [x] 8.1 Run `ruff check src/hecate/ tests/` — zero errors (1 pre-existing S101)
- [x] 8.2 Run `ruff format --check src/ tests/` — zero errors
- [x] 8.3 Run `mypy src/` — zero errors
- [x] 8.4 Run `python -m pytest tests/ -q` — all tests pass (excluding 5 pre-existing failures in test_citation_chat.py)
- [x] 8.5 Run `npm run lint` and `npm run build` in `web/` — zero errors (1 pre-existing warning)
