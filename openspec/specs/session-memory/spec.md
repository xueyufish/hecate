# Session Memory — In-Session Memory Integration

## Overview

Wire the existing three-layer memory services (L1 working memory, L2 conversation compression, L3 user memory) into ConversationService, giving Agents full memory capabilities in every conversation turn.

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
