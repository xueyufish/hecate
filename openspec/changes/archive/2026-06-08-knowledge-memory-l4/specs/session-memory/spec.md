## MODIFIED Requirements — 修改的需求

### Requirement: L4 Knowledge Memory Tools (ADDED) — 需求：L4 知识内存工具（新增）

当智能体启用了知识内存时，系统应注册两个智能体工具：`knowledge_insert` 和 `knowledge_search`。这些工具允许智能体在对话期间主动存储和检索知识。

#### Scenario: Agent inserts knowledge during conversation — 场景：智能体在对话期间插入知识
- **WHEN — 当** 智能体确定某条信息值得长期存储并调用 `knowledge_insert(content="...", tags=[...])`
- **THEN — 则** 系统创建 `KnowledgeMemoryModel`，生成嵌入，upsert 到 Qdrant，向智能体返回确认

#### Scenario: Agent searches knowledge during conversation — 场景：智能体在对话期间搜索知识
- **WHEN — 当** 智能体需要回忆先前存储的知识并调用 `knowledge_search(query="...", top_k=5)`
- **THEN — 则** 系统执行混合搜索，向智能体返回相关的知识内存作为工具结果

#### Scenario: Agent tool availability — 场景：智能体工具可用性
- **WHEN — 当** 智能体配置启用了知识内存（默认：启用）
- **THEN — 则** `knowledge_insert` 和 `knowledge_search` 工具在对话开始时注册到智能体的工具列表中

#### Scenario: Agent tool not available when disabled — 场景：禁用时智能体工具不可用
- **WHEN — 当** 智能体配置显式禁用知识内存
- **THEN — 则** 知识工具未注册，智能体无法访问 L4

### Requirement: L4 Knowledge in Conversation Flow (ADDED) — 需求：对话流中的 L4 知识（新增）

在每个对话轮次中，系统可以选择基于用户消息预取相关的知识内存并将其注入上下文。这是一个可配置的行为，非强制性的。

#### Scenario: Auto-inject knowledge context — 场景：自动注入知识上下文
- **WHEN — 当** 智能体设置 `auto_knowledge_inject=true` 且用户发送消息
- **THEN — 则** 系统使用用户的消息执行 `knowledge_search`，在 LLM 调用之前将前 K 个结果作为系统上下文注入

#### Scenario: No auto-inject — 场景：无自动注入
- **WHEN — 当** 智能体设置 `auto_knowledge_inject=false`（默认）
- **THEN — 则** 知识仅可通过显式的 `knowledge_search` 工具调用访问
