## 1. 数据模型与迁移

- [x] 1.1 在 `src/hecate/models/memory.py` 的 `MemoryBlockModel` 中添加 `workspace_id` 列——UUID 列带索引，nullable=False，默认零 UUID
- [x] 1.2 在 `src/hecate/models/memory.py` 的 `MemoryModel` 中添加 `workspace_id` 列——UUID 列带索引，nullable=False，默认零 UUID
- [x] 1.3 在 `src/hecate/models/memory.py` 中创建 `KnowledgeMemoryModel`——ORM 模型，包含字段：workspace_id、agent_id、content、tags（JSON）、importance（Float）、access_count（Integer）、source（String）、user_id（可选 UUID FK），通过 BaseModel 实现软删除
- [x] 1.4 在 `src/hecate/models/memory.py` 中为 L4 创建 Pydantic schemas——KnowledgeMemoryCreateSchema、KnowledgeMemoryUpdateSchema、KnowledgeMemoryReadSchema、KnowledgeMemorySearchSchema
- [x] 1.5 更新 `MemoryBlockCreateSchema` 和 `MemoryCreateSchema` 以包含 `workspace_id` 字段
- [x] 1.6 更新 `MemoryBlockReadSchema` 和 `MemoryReadSchema` 以包含 `workspace_id` 字段
- [ ] 1.7 创建 Alembic 迁移——向 `memory_blocks` 和 `memories` 表添加带默认零 UUID + 索引的 `workspace_id`；创建 `knowledge_memories` 表
- [x] 1.8 在 `tests/conftest.py` 中注册 `KnowledgeMemoryModel` 用于测试数据库设置

## 2. 内存隔离——服务层更新

- [x] 2.1 更新 `src/hecate/services/memory/working_memory.py` 中的 `WorkingMemoryService`——向所有方法添加 `workspace_id` 参数，所有查询按 workspace_id 过滤
- [x] 2.2 更新 `src/hecate/services/memory/user_memory.py` 中的 `UserMemoryService`——向所有方法添加 `workspace_id` 参数，所有查询按 workspace_id 过滤
- [x] 2.3 更新 `MemoryBlockModel` 创建路径——创建块时从智能体的工作空间设置 workspace_id
- [x] 2.4 更新 `MemoryModel` 创建路径——从认证上下文创建内存时设置 workspace_id

## 3. L4 知识内存服务

- [x] 3.1 创建 `src/hecate/services/memory/knowledge_memory.py`——`KnowledgeMemoryService` 类，`__init__(db, vector_store)` 接受 AsyncSession 和 VectorStore
- [x] 3.2 实现 `insert_knowledge(agent_id, workspace_id, content, tags, importance, user_id, source)`——创建 KnowledgeMemoryModel 行，通过 embedding_service 生成嵌入，upsert 到 Qdrant 集合
- [x] 3.3 实现 `search_knowledge(agent_id, workspace_id, query, top_k, tags, user_id, mode)`——通过 workspace_id + agent_id 负载过滤器在 Qdrant 上进行混合搜索，返回评分结果
- [x] 3.4 实现 `get_knowledge(agent_id, workspace_id, memory_id)`——检索单个知识内存，带工作空间 + 智能体所有权检查
- [x] 3.5 实现 `list_knowledge(agent_id, workspace_id, tags, limit, offset)`——带分页、标签过滤器、按 updated_at desc 排序的列出
- [x] 3.6 实现 `delete_knowledge(agent_id, workspace_id, memory_id)`——PostgreSQL 中的软删除 + 从 Qdrant 删除点
- [x] 3.7 实现 `_ensure_collection()`——在首次写入时惰性创建 Qdrant 集合 `hecate_knowledge_memories`，带稠密 + 稀疏向量支持
- [x] 3.8 实现 `_upsert_to_qdrant(memory)`——生成嵌入，构建负载 {workspace_id, agent_id, tags, importance, user_id, text}，upsert 到 Qdrant
- [x] 3.9 实现重复检测——插入时检查是否有相同 content + agent_id 的现有知识，如果找到则更新现有而非创建重复

## 4. API 层

- [x] 4.1 更新 `src/hecate/api/management/memory.py` 中的 L1 内存块端点——将 agent 查找中的 workspace_id 传递给 WorkingMemoryService 调用
- [x] 4.2 更新 `src/hecate/api/management/memory.py` 中的 L3 用户内存端点——将认证上下文中的 workspace_id 传递给 UserMemoryService 调用
- [x] 4.3 创建 L4 知识内存端点——`POST /api/agents/{agent_id}/knowledge`（创建）、`GET`（列出）、`GET /{memory_id}`（获取）、`DELETE /{memory_id}`（删除）、`POST /search`（搜索）
- [x] 4.4 在 `src/hecate/main.py` 中注册知识内存路由——已注册（memory_router 在第 152 行）

## 5. 智能体工具

- [x] 5.1 创建 `src/hecate/services/memory/knowledge_tools.py`——定义 `knowledge_insert` 和 `knowledge_search` 工具 schemas（用于 LLM 工具调用的 JSON Schema）
- [ ] 5.2 实现 `knowledge_insert` 工具处理器——解析参数，调用 KnowledgeMemoryService.insert_knowledge，返回确认
- [ ] 5.3 实现 `knowledge_search` 工具处理器——解析参数，调用 KnowledgeMemoryService.search_knowledge，返回格式化结果
- [ ] 5.4 连接工具注册——在启用知识内存时将 knowledge 工具注册到智能体工具列表

## 6. 测试

- [x] 6.1 测试 `KnowledgeMemoryModel` ORM——创建、读取、更新、软删除、workspace_id 过滤
- [x] 6.2 测试 `KnowledgeMemoryService`——insert_knowledge、search_knowledge、list_knowledge、delete_knowledge、重复检测
- [x] 6.3 测试工作空间隔离——验证 L1/L3/L4 查询仅返回正确工作空间内的数据
- [x] 6.4 测试 L4 API 端点——通过 httpx AsyncClient 进行 CRUD + 搜索，验证工作空间范围
- [x] 6.5 测试更新后的 L1/L3 API——验证现有内存块和用户内存端点上的 workspace_id 过滤
- [ ] 6.6 测试 Qdrant 集成——验证集合创建、负载结构、带工作空间过滤器的混合搜索
- [x] 6.7 测试知识工具——验证工具 schema、插入处理器、搜索处理器
