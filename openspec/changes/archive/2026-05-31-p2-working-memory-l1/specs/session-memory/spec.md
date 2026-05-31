## MODIFIED Requirements

### REQ-1: L1 Working Memory Injection
ConversationService calls `WorkingMemoryService.list_blocks(agent_id)` before each `assemble()` to load all memory blocks for that Agent. Pass the block list to `ContextAssembler.assemble(memory_blocks=...)`. Agents can update memory blocks via the `update_memory_block(label, content)` tool. The frontend SHALL display active memory block labels as badges in the chat page header.

#### Scenario 1: Memory blocks loaded each turn
- **WHEN** a user sends a message to an agent with memory blocks configured
- **THEN** ConversationService SHALL load all non-deleted blocks for that agent and pass to ContextAssembler

#### Scenario 2: Agent updates memory block
- **WHEN** an agent calls update_memory_block("current_task", "new task description")
- **THEN** the memory block SHALL be updated in the database and available on the next turn

#### Scenario 3: Frontend shows active blocks
- **WHEN** a user chats with an agent that has memory blocks
- **THEN** the chat page SHALL display badges showing the active block labels
