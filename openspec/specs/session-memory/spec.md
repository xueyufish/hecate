# Session Memory — In-Session Memory Integration

## Overview

Wire the existing three-layer memory services (L1 working memory, L2 conversation compression, L3 user memory, L4 knowledge memory) into ConversationService, giving Agents full memory capabilities in every conversation turn.

## Requirements

### REQ-1: L1 Working Memory Injection

- ConversationService calls `WorkingMemoryService.list_blocks(agent_id)` before each `assemble()` to load all memory blocks for that Agent
- Pass the block list to `ContextAssembler.assemble(memory_blocks=...)`
- Agents can update memory blocks via the `update_memory_block(label, content)` tool
- The frontend SHALL display active memory block labels as badges in the chat page header

### REQ-2: L2 Conversation Compression

- ConversationService checks the current message token count during `assemble()`
- When token count exceeds `compression_threshold` (default 4000), call `CompressionPipeline.compress()` to compress history
- Compressed messages replace original messages when sent to the LLM; original messages are kept in DB
- Compression history is queryable after session ends (compression level, tokens saved)

### REQ-3: L3 User Memory Extraction and Retrieval

- After Assistant response, call `UserMemoryService.extract_facts(user_id, messages)` to extract new facts from the conversation
- Call `store_memory()` to persist extracted facts
- On the next turn, call `retrieve_memories(user_id, query)` to get relevant user memories and inject into context

### REQ-4: Memory Tool Registration

- Register `update_memory_block` tool in Agent tool list (when Agent has working memory configured)
- Register `search_user_memory` tool (when user has L3 memory enabled)

### REQ-5: L4 Knowledge Memory Tools

- When an agent has knowledge memory enabled, register two agent tools: `knowledge_insert` and `knowledge_search`
- `knowledge_insert(content, tags)` creates a KnowledgeMemoryModel, generates embedding, upserts to Qdrant
- `knowledge_search(query, top_k=5)` performs hybrid search and returns relevant knowledge memories
- Tools are registered at conversation start based on agent configuration
- When agent configuration explicitly disables knowledge memory, tools are not registered

### REQ-6: L4 Auto-Inject Knowledge Context

- When agent has `auto_knowledge_inject=true`, the system MAY pre-fetch relevant knowledge memories based on user message and inject them into context on each turn
- Default is `auto_knowledge_inject=false` — knowledge only accessible via explicit tool calls

## Scenarios

### Scenario 1: Long Conversation Auto-Compression

```
Given Agent has 20 turns of conversation history (~6000 tokens)
When User sends a new message
Then System detects token count exceeds threshold
And Calls CompressionPipeline to auto-compress history
And Uses compressed context to call LLM
And Original messages are preserved in DB
```

### Scenario 2: Cross-Session User Preference Memory

```
Given User mentions "I like using Python" in session A
When System extracts and stores user memory {fact: "User likes Python", category: "preference"}
And User asks "Help me write a script" in session B
Then System retrieves user preference, injects into context
And Agent writes the script in Python
```

### Scenario 3: Agent Proactively Updates Working Memory

```
Given Agent has working memory block "current_task"
When Agent detects task change during execution
And Agent calls update_memory_block("current_task", "new task description")
Then Working memory block is updated
And Agent can read the updated memory in the next turn
```

### Scenario 4: Agent Stores Knowledge During Conversation

```
Given Agent has knowledge memory enabled
When Agent determines a fact is worth long-term storage and calls knowledge_insert(content="Company policy: 2FA required", tags=["policy"])
Then System creates KnowledgeMemoryModel, generates embedding, upserts to Qdrant
And Returns confirmation to agent
```

### Scenario 5: Agent Searches Knowledge During Conversation

```
Given Agent has previously stored 10 knowledge memories
When Agent needs to recall stored knowledge and calls knowledge_search(query="security requirements", top_k=3)
Then System performs hybrid search
And Returns relevant knowledge memories as tool result
```

### Scenario 6: Auto Knowledge Context Injection

```
Given Agent has auto_knowledge_inject=true
When User sends a message about "password policy"
Then System performs knowledge_search with the user's message
And Injects top-K results as system context before the LLM call
```
