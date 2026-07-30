## MODIFIED Requirements — 修改的需求

### REQ-1: L1 Working Memory Injection — L1 工作内存注入
ConversationService 在每次 `assemble()` 之前调用 `WorkingMemoryService.list_blocks(agent_id)` 以加载该 Agent 的所有内存块。将块列表传递给 `ContextAssembler.assemble(memory_blocks=...)`。Agent 可以通过 `update_memory_block(label, content)` 工具更新内存块。前端应在聊天页面头部将活跃内存块标签显示为徽章。

#### Scenario 1: Memory blocks loaded each turn — 场景 1：每轮对话加载内存块
- **WHEN** 用户向配置了内存块的 Agent 发送消息
- **THEN** ConversationService 应加载该 Agent 的所有未删除块并传递给 ContextAssembler

#### Scenario 2: Agent updates memory block — 场景 2：Agent 更新内存块
- **WHEN** Agent 调用 update_memory_block("current_task", "new task description")
- **THEN** 内存块应在数据库中更新，并在下一轮对话中可用

#### Scenario 3: Frontend shows active blocks — 场景 3：前端显示活跃块
- **WHEN** 用户与有内存块的 Agent 聊天
- **THEN** 聊天页面应显示徽章，展示活跃块的标签
