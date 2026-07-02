## MODIFIED Requirements

### Requirement: L4 Knowledge Memory Tools (ADDED)

When an agent has knowledge memory enabled, the system SHALL register two agent tools: `knowledge_insert` and `knowledge_search`. These tools allow the agent to actively store and retrieve knowledge during conversations.

#### Scenario: Agent inserts knowledge during conversation
- **WHEN** Agent determines a piece of information is worth long-term storage and calls `knowledge_insert(content="...", tags=[...])`
- **THEN** System creates a `KnowledgeMemoryModel`, generates embedding, upserts to Qdrant, returns confirmation to agent

#### Scenario: Agent searches knowledge during conversation
- **WHEN** Agent needs to recall previously stored knowledge and calls `knowledge_search(query="...", top_k=5)`
- **THEN** System performs hybrid search, returns relevant knowledge memories to the agent as tool result

#### Scenario: Agent tool availability
- **WHEN** Agent configuration has knowledge memory enabled (default: enabled)
- **THEN** `knowledge_insert` and `knowledge_search` tools are registered in the agent's tool list at conversation start

#### Scenario: Agent tool not available when disabled
- **WHEN** Agent configuration explicitly disables knowledge memory
- **THEN** Knowledge tools are not registered, and agent cannot access L4

### Requirement: L4 Knowledge in Conversation Flow (ADDED)

On each conversation turn, the system MAY optionally pre-fetch relevant knowledge memories based on the user's message and inject them into context. This is a configurable behavior, not mandatory.

#### Scenario: Auto-inject knowledge context
- **WHEN** Agent has `auto_knowledge_inject=true` and user sends a message
- **THEN** System performs `knowledge_search` with the user's message, injects top-K results as system context before the LLM call

#### Scenario: No auto-inject
- **WHEN** Agent has `auto_knowledge_inject=false` (default)
- **THEN** Knowledge is only accessible via explicit `knowledge_search` tool calls
