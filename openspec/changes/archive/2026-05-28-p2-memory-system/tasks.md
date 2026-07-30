## 1. 数据模型

- [x] 1.1 在 `models/memory.py` 中创建 `MemoryBlockModel` ORM——字段：id, agent_id, label, content, position, limit, created_at, updated_at
- [x] 1.2 在 `models/memory.py` 中创建 `MemoryModel` ORM——字段：id, content, scope(JSONB), memory_type, importance, access_count, embedding(vector), created_at
- [x] 1.3 创建 Pydantic schema：MemoryBlockCreateSchema, MemoryBlockUpdateSchema, MemoryBlockReadSchema, MemoryCreateSchema, MemoryReadSchema
- [x] 1.4 为 memory_blocks 和 memories 表生成 Alembic 迁移
- [x] 1.5 更新 `alembic/env.py` 导入 memory 模型

## 2. L1 工作记忆

- [x] 2.1 创建 `services/memory/working_memory.py` 及 WorkingMemoryService
- [x] 2.2 实现 `create_block(agent_id, label, content, position, limit)`——创建 MemoryBlockModel
- [x] 2.3 实现 `get_block(agent_id, block_id)`——返回 block
- [x] 2.4 实现 `update_block(agent_id, block_id, content)`——更新 block 内容
- [x] 2.5 实现 `delete_block(agent_id, block_id)`——删除 block
- [x] 2.6 实现 `list_blocks(agent_id)`——按 position 排序返回所有 blocks
- [x] 2.7 实现 `inject_blocks(messages, blocks)`——在配置的位置将 blocks 插入 messages

## 3. L2 对话压缩

- [x] 3.1 创建 `services/memory/compression.py` 及 CompressionPipeline
- [x] 3.2 实现 `snip(messages, recent_window)`——移除低价值消息，保留最近 N 条
- [x] 3.3 实现 `microcompact(messages)`——合并连续的同角色消息
- [x] 3.4 实现 `autocompact(messages, model)`——LLM 对较早消息的摘要
- [x] 3.5 实现 `compress(messages, budget, model)`——编排 snip→microcompact→autocompact
- [x] 3.6 将压缩集成到 ContextAssembler——替代 P1 的简单截断

## 4. L3 用户记忆

- [x] 4.1 创建 `services/memory/user_memory.py` 及 UserMemoryService
- [x] 4.2 实现 `extract_facts(messages, model)`——LLM 工具调用提取事实
- [x] 4.3 实现 `store_memory(content, scope, memory_type)`——生成 embedding，持久化到 DB
- [x] 4.4 实现 `retrieve_memories(query, scope, top_k)`——向量相似度搜索
- [x] 4.5 实现 `update_importance(memory_id, boost)`——在访问时调整重要性
- [x] 4.6 实现 `delete_memory(memory_id)`——软删除

## 5. API 层

- [x] 5.1 创建 `api/management/memory.py` 及 memory block 端点
- [x] 5.2 实现 `POST /api/agents/{id}/memory-blocks`——创建 block
- [x] 5.3 实现 `GET /api/agents/{id}/memory-blocks`——列出 blocks
- [x] 5.4 实现 `GET /api/agents/{id}/memory-blocks/{block_id}`——获取 block
- [x] 5.5 实现 `PUT /api/agents/{id}/memory-blocks/{block_id}`——更新 block
- [x] 5.6 实现 `DELETE /api/agents/{id}/memory-blocks/{block_id}`——删除 block
- [x] 5.7 实现 `POST /api/memory`——创建记忆（提取或手动）
- [x] 5.8 实现 `GET /api/memory`——列出/搜索记忆
- [x] 5.9 实现 `DELETE /api/memory/{id}`——删除记忆
- [x] 5.10 在主 FastAPI 应用中注册 memory 路由

## 6. 上下文集成

- [x] 6.1 修改 ContextAssembler 将 MemoryBlocks 注入上下文
- [x] 6.2 修改 ContextAssembler 将相关 L3 记忆注入上下文
- [x] 6.3 用 L2 压缩管道替代 P1 的简单截断
- [x] 6.4 向 BudgetManager 添加记忆 token 预算跟踪

## 7. 测试

- [x] 7.1 WorkingMemoryService 单元测试——CRUD, inject_blocks
- [x] 7.2 CompressionPipeline 单元测试——snip, microcompact, autocompact
- [x] 7.3 UserMemoryService 单元测试——extract, store, retrieve
- [x] 7.4 memory block API 端点的集成测试
- [x] 7.5 memory API 端点的集成测试
- [x] 7.6 带记忆注入的上下文组装集成测试
