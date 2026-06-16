## MODIFIED Requirements

### Requirement: L1 Working Memory Injection

- ConversationService SHALL call `WorkingMemoryService.list_blocks(agent_id, workspace_id)` before each `assemble()` to load all memory blocks for that Agent
- The `workspace_id` parameter SHALL be auto-injected from the authenticated workspace context, not passed manually
- ConversationService SHALL pass the block list to `ContextAssembler.assemble(memory_blocks=...)`
- Agents SHALL be able to update memory blocks via the `update_memory_block(label, content)` tool

#### Scenario: Workspace-scoped memory block loading
- **WHEN** ConversationService loads memory blocks for an agent in workspace W1
- **THEN** only blocks with `workspace_id = W1.id` are returned

### Requirement: L3 User Memory Extraction and Retrieval

- After Assistant response, ConversationService SHALL call `UserMemoryService.extract_facts(user_id, messages)` to extract new facts from the conversation
- ConversationService SHALL call `store_memory()` to persist extracted facts, with `workspace_id` auto-set from the auth context
- On the next turn, ConversationService SHALL call `retrieve_memories(user_id, query)` to get relevant user memories scoped to the authenticated workspace and inject them into context

#### Scenario: User memory scoped to workspace
- **WHEN** user memories are stored and retrieved in workspace W1
- **THEN** only memories with `workspace_id = W1.id` are returned, even if the same user has memories in other workspaces

### Requirement: L4 Knowledge Memory Tools

- When an agent has knowledge memory enabled, the system SHALL register two agent tools: `knowledge_insert` and `knowledge_search`
- `knowledge_insert(content, tags)` SHALL create a KnowledgeMemoryModel with `workspace_id` auto-set from auth context, generate embedding, and upsert to Qdrant
- `knowledge_search(query, top_k=5)` SHALL perform hybrid search scoped to the authenticated workspace_id
- Tools SHALL be registered at conversation start based on agent configuration

#### Scenario: Knowledge insert auto-scope
- **WHEN** an agent calls `knowledge_insert` in workspace W1
- **THEN** the created KnowledgeMemoryModel has `workspace_id = W1.id` automatically, no manual workspace_id needed

#### Scenario: Knowledge search workspace-scoped
- **WHEN** an agent calls `knowledge_search` in workspace W1
- **THEN** the search only returns knowledge memories with `workspace_id = W1.id`
