## MODIFIED Requirements — 修改的需求

### 需求：L1 工作内存注入

- ConversationService 应在每次 `assemble()` 前调用 `WorkingMemoryService.list_blocks(agent_id, workspace_id)`，加载该代理的所有内存块
- `workspace_id` 参数应从认证的工作区上下文自动注入，而非手动传递
- ConversationService 应将块列表传递给 `ContextAssembler.assemble(memory_blocks=...)`
- 代理应能通过 `update_memory_block(label, content)` 工具更新内存块

#### 场景：工作区限定的内存块加载
- **当** ConversationService 为工作区 W1 中的代理加载内存块
- **则** 仅返回 `workspace_id = W1.id` 的块

### 需求：L3 用户内存提取与检索

- 在助手响应后，ConversationService 应调用 `UserMemoryService.extract_facts(user_id, messages)` 从对话中提取新事实
- ConversationService 应调用 `store_memory()` 持久化提取的事实，`workspace_id` 从认证上下文自动设置
- 在下一轮对话中，ConversationService 应调用 `retrieve_memories(user_id, query)` 获取限定于认证工作区的相关用户内存，并将其注入上下文中

#### 场景：用户内存限定于工作区
- **当** 用户内存在工作区 W1 中存储和检索
- **则** 即使同一用户在其他工作区也有内存，仅返回 `workspace_id = W1.id` 的内存

### 需求：L4 知识内存工具

- 当代理启用了知识内存时，系统应注册两个代理工具：`knowledge_insert` 和 `knowledge_search`
- `knowledge_insert(content, tags)` 应创建 KnowledgeMemoryModel，`workspace_id` 从认证上下文自动设置，生成嵌入，并 upsert 到 Qdrant
- `knowledge_search(query, top_k=5)` 应执行限定于认证 workspace_id 的混合搜索
- 工具应在对话开始时基于代理配置注册

#### 场景：知识插入自动限定作用域
- **当** 代理在工作区 W1 中调用 `knowledge_insert`
- **则** 创建的 KnowledgeMemoryModel 自动具有 `workspace_id = W1.id`，无需手动指定 workspace_id

#### 场景：知识搜索限定于工作区
- **当** 代理在工作区 W1 中调用 `knowledge_search`
- **则** 搜索仅返回 `workspace_id = W1.id` 的知识内存
