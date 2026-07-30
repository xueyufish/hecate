## Why — 为什么

Hecate 的内存系统有 L1（工作内存）、L2（会话压缩）和 L3（用户事实），但缺少 L4——一个长期的、智能体可以跨对话存储和检索累积知识的知识档案。此外，所有现有的内存模型（L1 MemoryBlockModel、L3 MemoryModel）都缺少多租户隔离所需的 `workspace_id`，这是平台的基本要求。

## What Changes — 变更内容

- **新增：知识内存（L4）**——智能体范围的长周期知识存储，带语义检索。智能体通过工具调用（`knowledge_insert`）主动写入事实，并通过搜索（`knowledge_search`）检索。存储在 Qdrant（专用集合）中，用于混合向量+BM25 搜索，附带 PostgreSQL 元数据。
- **修复：L1 和 L3 的租户隔离**——向 `MemoryBlockModel`（L1）和 `MemoryModel`（L3）添加 `workspace_id` 作为一等列。所有现有查询更新为按工作空间过滤。
- **新增：L4 REST API**——智能体知识内存的 CRUD + 搜索端点，工作空间范围限定。
- **新增：Alembic 迁移**——向 `memory_blocks` 和 `memories` 表添加 `workspace_id` 列 + 索引；创建新的 `knowledge_memories` 表。
- **新增：内存隔离执行**——所有内存 API 端点通过认证上下文验证工作空间所有权。

## Capabilities — 能力

### New Capabilities — 新能力

- `knowledge-memory`：L4 知识内存存储、检索和智能体工具接口——Qdrant 后端的混合搜索，附带 PostgreSQL 元数据、工作空间范围隔离、原子事实粒度
- `memory-isolation`：向 L1（MemoryBlockModel）和 L3（MemoryModel）添加 `workspace_id` 一等列，更新所有服务和 API 以执行租户隔离

### Modified Capabilities — 修改的能力

- `memory-api`：添加 L4 知识内存端点（CRUD + 搜索），更新现有 L1/L3 端点以接受和验证工作空间上下文
- `session-memory`：将 L4 知识内存接入对话流程——启用 L4 时，智能体注册 `knowledge_insert` 和 `knowledge_search` 工具

## Impact — 影响

- **数据库**：Alembic 迁移向 2 个现有表添加列 + 创建 1 个新表。现有行获得 `workspace_id = UUID('00000000-0000-0000-0000-000000000000')`（默认工作空间）。
- **服务**：`WorkingMemoryService`、`UserMemoryService`——所有方法增加 `workspace_id` 参数。新的 `KnowledgeMemoryService` 用于 L4。
- **API**：所有 `/api/agents/{id}/memory-blocks`、`/api/memory`、`/api/users/{id}/memories` 端点增加工作空间过滤。新的 `/api/agents/{id}/knowledge` 端点。
- **RAG/Qdrant**：新的专用集合 `hecate_knowledge_memories`，带基于负载的工作空间过滤。
- **依赖**：无新的外部依赖——重用现有的 `embedding_service`、`HybridSearcher` 和来自 `services/rag/` 的 `VectorStore`。
