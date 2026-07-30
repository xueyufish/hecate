## Context — 上下文

Hecate 的内存系统目前有三层：

- **L1 工作内存**（`memory_blocks` 表中的 `MemoryBlockModel`）——每个智能体的命名上下文块，注入上下文窗口。无 `workspace_id`。
- **L2 会话内存**（压缩管道）——带自动压缩的对话历史。不是一个单独的模型；操作在 `ConversationModel` 上。
- **L3 用户内存**（`memories` 表中的 `MemoryModel`）——带模拟嵌入的用户事实。使用 `scope` JSONB 进行隔离，但无 `workspace_id` 列。

现有的 RAG 基础设施提供 `HybridSearcher`（稠密 + 稀疏 + RRF 融合）基于 Qdrant、用于向量生成的 `embedding_service` 以及带 Qdrant/Chroma 实现的 `VectorStore` ABC。

系统中所有其他模型（Agent、Tool、Knowledge、Prompt、Skill、Workflow）都有 `workspace_id` 作为一等列。内存模型是例外。

## Goals / Non-Goals — 目标 / 非目标

**Goals — 目标：**

- 实现 L4 知识内存——智能体范围、长期知识档案，带混合搜索检索
- 向 L1（MemoryBlockModel）和 L3（MemoryModel）添加 `workspace_id` 以实现多租户隔离
- 创建新的 `KnowledgeMemoryModel`（L4），从一开始就包含 `workspace_id`
- 重用现有 RAG 基础设施（embedding_service、HybridSearcher、VectorStore）进行 L4 检索
- 提供智能体工具（`knowledge_insert`、`knowledge_search`）用于 L4 交互
- 提供用于 L4 CRUD 和搜索的 REST API

**Non-Goals — 非目标：**

- 内存合并/去重（推迟到 P4 功能 4.5 内存集成）
- 基于 LLM 的内存提取（L3 目前使用启发式提取；L4 使用智能体发起的写入）
- 图结构内存（推迟到 P3 功能 4.3a 内存引擎增强）
- 将 L3 从模拟嵌入迁移到真实嵌入（单独的关注点）
- RBAC 或权限模型变更（workspace_id 对于租户隔离足够；RBAC 是 P3 功能 10.2）

## Decisions — 决策

### D1: L4 存储——双存储（PostgreSQL 元数据 + Qdrant 向量）

**Choice — 选择**：将 L4 知识存储为 PostgreSQL 行（元数据、全文）+ Qdrant 点（嵌入），在专用集合 `hecate_knowledge_memories` 中。

**Rationale — 理由**：匹配 Hecate 的现有模式，Qdrant 处理向量操作，PostgreSQL 处理元数据/过滤。RAG 管道已经使用此双存储模式。

**Alternatives considered — 考虑的替代方案**：
- 纯 Qdrant（无 SQL）——失去事务性元数据操作、过滤和连接
- 纯 PostgreSQL 带 pgvector——需要更改向量存储后端，失去混合搜索能力
- 与 RAG 文档共享集合——命名空间污染，不同的检索管道（按研究拒绝）

### D2: L4 写入机制——仅智能体工具调用

**Choice — 选择**：L4 内存仅通过智能体工具调用（`knowledge_insert`）写入。无自动后台提取。

**Rationale — 理由**：L4 是"智能体的知识档案"——智能体决定什么值得长期记住。这匹配 Letta 的方法。自动提取更适合 L3（用户偏好）。

**Alternatives considered — 考虑的替代方案**：
- 从对话中自动提取——模糊了 L3/L4 边界；L3 已经在做提取
- 混合（自动+手动）——增加了复杂性，对 MVP 没有明显好处

### D3: L4 粒度——原子事实，非段落

**Choice — 选择**：每个 L4 内存是一个单一的原子事实/声明（例如，"公司报销需要经理批准"）。

**Rationale — 理由**：原子事实便于未来合并（重复检测、合并）。段落级别的粒度使去重非常困难。匹配 Mem0 的方法。

**Alternatives considered — 考虑的替代方案**：
- 段落级别（Letta 风格）——存储更简单但以后更难合并
- 混合——对 MVP 过于复杂

### D4: L4 检索——重用 Qdrant 上的 HybridSearcher

**Choice — 选择**：L4 检索使用 `HybridSearcher` 和 `embedding_service` 对专用 Qdrant 集合进行稠密+稀疏+RRF 混合搜索。

**Rationale — 理由**：Hecate 已经有一个生产质量的混合搜索管道。无需构建单独的检索机制。`HybridSearcher` 支持稠密、稀疏和混合模式。

**Alternatives considered — 考虑的替代方案**：
- 纯稠密搜索（Letta 风格）——检索质量较弱；遗漏关键词精确匹配
- 自定义多信号评分器——不必要的复杂性；HybridSearcher 已经提供 RRF 融合

### D5: 租户隔离——`workspace_id` 作为所有内存模型的一等列

**Choice — 选择**：向 `MemoryBlockModel`（L1）、`MemoryModel`（L3）和 `KnowledgeMemoryModel`（L4）添加带索引的 `workspace_id` UUID 列。所有服务查询按工作空间过滤。Qdrant 负载包含 `workspace_id` 用于向量级过滤。

**Rationale — 理由**：与系统中所有其他模型一致（Agent、Tool、Knowledge 等）。一等列支持高效的索引查询。基于 JSONB 的隔离（当前 L3 方法）较慢且查询友好性较差。

**Alternatives considered — 考虑的替代方案**：
- 仅保留 scope JSONB——与其他模型不一致，索引性能差
- 仅 Qdrant 负载中的 workspace_id——无 SQL 级别的执行，更难审计

### D6: L4 范围——智能体范围，带可选用户上下文

**Choice — 选择**：L4 内存主要按 `agent_id`（智能体的累积知识）限定范围。每个内存有一个可选的 `user_id` 字段，用于关于特定用户的知识。

**Rationale — 理由**：L4 是智能体的知识，而非用户的知识。但智能体可能累积特定于用户的知识（例如，"客户 A 使用 MySQL 8.0"），这些知识应可按用户检索。

### D7: L3 `scope` JSONB——与新的 `workspace_id` 一起保留

**Choice — 选择**：向 `MemoryModel`（L3）添加 `workspace_id` 作为一等列，同时保留现有的 `scope` JSONB 字段。

**Rationale — 理由**：`workspace_id` 处理租户隔离。`scope` 以更细粒度处理租户内过滤（user_id、agent_id、session_id）。它们服务于不同的目的，应共存。

### D8: Alembic 迁移策略——现有行的默认工作空间 UUID

**Choice — 选择**：迁移向现有行添加 `workspace_id` 列，服务器默认值为 `UUID('00000000-0000-0000-0000-000000000000')`。新行需要显式的 `workspace_id`。

**Rationale — 理由**：匹配其他模型（Agent、Tool 等）中使用的模式，其中 `workspace_id` 默认为零 UUID。无数据丢失，向后兼容。

## Risks / Trade-offs — 风险 / 权衡

- **[风险] 大型 `memories` 表上的迁移** → 缓解措施：使用服务器默认值添加列（无需重写），并发添加索引。零停机迁移。
- **[风险] Qdrant 集合创建时机** → 缓解措施：`KnowledgeMemoryService` 在首次写入时惰性创建集合（如果不存在），使用 `VectorStore.create_collection()`。
- **[风险] 写入时的 L4 嵌入延迟** → 缓解措施：`embedding_service.encode()` 已经是异步的。写入路径是智能体发起的（不延迟关键）。
- **[权衡] MVP 中无合并** → 可接受：内存累积而不去重。可以以后清理或通过手动删除。匹配 Letta 和 LangGraph 的方法。
- **[权衡] 智能体必须显式写入 L4** → 可接受：智能体提示可以指导何时使用 `knowledge_insert`。未来的增强可以添加自动触发器。
