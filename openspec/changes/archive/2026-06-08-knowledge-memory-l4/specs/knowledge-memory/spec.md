## ADDED Requirements — 新增的需求

### Requirement: Knowledge Memory Storage — 需求：知识内存存储

系统应提供 `KnowledgeMemoryModel`（ORM）和 `KnowledgeMemoryService`，用于将智能体知识存储为原子事实。每个知识内存应具有：`workspace_id`（租户范围）、`agent_id`（智能体范围）、`content`（事实文本）、`tags`（用于分类的 JSON 数组）、`importance`（0.0-1.0 分数）、`access_count`（检索频率）、`source`（创建方式："agent_tool" 或 "api"）、`user_id`（可选，用于特定用户的知识），以及存储在 Qdrant 中的 `embedding`。

#### Scenario: Agent inserts knowledge via tool call — 场景：智能体通过工具调用插入知识
- **WHEN — 当** 智能体调用 `knowledge_insert(content="公司报销需要经理批准", tags=["policy", "reimbursement"])`
- **THEN — 则** 系统使用内容创建新的 `KnowledgeMemoryModel` 行，通过 `embedding_service` 生成嵌入，upsert 到 Qdrant 集合 `hecate_knowledge_memories`，负载为 `{workspace_id, agent_id, tags, importance}`，并返回内存 ID

#### Scenario: Knowledge with user context — 场景：带用户上下文的知识
- **WHEN — 当** 智能体调用 `knowledge_insert(content="客户 A 使用 MySQL 8.0", user_id="uuid-of-customer-a")`
- **THEN — 则** 系统存储带可选 `user_id` 字段的知识，用于特定用户检索

#### Scenario: Duplicate content prevention — 场景：重复内容预防
- **WHEN — 当** 智能体使用与同一智能体现有知识内存相同的内容（规范化后）调用 `knowledge_insert`
- **THEN — 则** 系统应更新现有内存的 `updated_at` 时间戳并增加 `access_count`，而非创建重复

### Requirement: Knowledge Memory Hybrid Search — 需求：知识内存混合搜索

系统应使用 `HybridSearcher` 在专用 Qdrant 集合上提供知识内存的语义检索。搜索应支持稠密（向量）、稀疏（BM25）和混合（RRF 融合）模式。

#### Scenario: Agent searches knowledge — 场景：智能体搜索知识
- **WHEN — 当** 智能体调用 `knowledge_search(query="报销政策", top_k=5)`
- **THEN — 则** 系统生成查询嵌入，对 Qdrant 集合执行混合搜索，按 `{workspace_id, agent_id}` 过滤，返回按相关性分数排序的前 K 个结果，包含内容和元数据

#### Scenario: Search with user context — 场景：带用户上下文的搜索
- **WHEN — 当** 智能体调用 `knowledge_search(query="数据库偏好", user_id="uuid-of-customer-a")`
- **THEN — 则** 系统添加 `user_id` 过滤器以将结果缩小到关于该特定用户的知识

#### Scenario: Search with tag filter — 场景：带标签过滤器的搜索
- **WHEN — 当** 智能体调用 `knowledge_search(query="政策", tags=["reimbursement"])`
- **THEN — 则** 系统过滤结果，仅包含具有匹配标签的内存

### Requirement: Knowledge Memory CRUD API — 需求：知识内存 CRUD API

系统应为知识内存管理提供 REST API 端点，全部限定工作空间范围。

#### Scenario: List knowledge memories for an agent — 场景：列出智能体的知识内存
- **WHEN — 当** 调用 `GET /api/agents/{agent_id}/knowledge`
- **THEN — 则** 返回智能体的分页知识内存列表，按 `updated_at` 降序排列

#### Scenario: Get specific knowledge memory — 场景：获取特定的知识内存
- **WHEN — 当** 调用 `GET /api/agents/{agent_id}/knowledge/{memory_id}`
- **THEN — 则** 返回知识内存详细信息，包括内容、标签、重要性、access_count、来源

#### Scenario: Create knowledge memory via API — 场景：通过 API 创建知识内存
- **WHEN — 当** 调用 `POST /api/agents/{agent_id}/knowledge`，包含 `{"content": "...", "tags": [...], "importance": 0.8}`
- **THEN — 则** 创建新的知识内存，生成嵌入，存储在 Qdrant 中，返回 201 及创建的内存

#### Scenario: Delete knowledge memory — 场景：删除知识内存
- **WHEN — 当** 调用 `DELETE /api/agents/{agent_id}/knowledge/{memory_id}`
- **THEN — 则** 在 PostgreSQL 中软删除内存，并从 Qdrant 删除点

#### Scenario: Search knowledge memories via API — 场景：通过 API 搜索知识内存
- **WHEN — 当** 调用 `POST /api/agents/{agent_id}/knowledge/search`，包含 `{"query": "...", "top_k": 5, "tags": [...]}`
- **THEN — 则** 执行混合搜索并返回评分结果

### Requirement: Qdrant Collection Management — 需求：Qdrant 集合管理

系统应使用专用 Qdrant 集合 `hecate_knowledge_memories` 用于 L4 知识向量。如果集合不存在，应在首次写入时惰性创建。

#### Scenario: First knowledge insert creates collection — 场景：首次知识插入创建集合
- **WHEN — 当** 首次调用 `knowledge_insert` 且集合不存在
- **THEN — 则** 系统在 upsert 之前调用 `VectorStore.create_collection("hecate_knowledge_memories")`，带稠密 + 稀疏向量支持

#### Scenario: Collection payload schema — 场景：集合负载 schema
- **WHEN — 当** 知识内存被 upsert 到 Qdrant
- **THEN — 则** 点负载应包括：`workspace_id`（UUID 字符串）、`agent_id`（UUID 字符串）、`tags`（JSON 数组）、`importance`（浮点数）、`user_id`（可选 UUID 字符串）、`text`（用于稀疏检索的内容）
