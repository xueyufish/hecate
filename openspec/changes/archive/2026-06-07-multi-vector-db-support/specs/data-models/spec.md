## MODIFIED Requirements — 修改的需求

### Requirement: KnowledgeBaseModel with embedding and search config — 需求：带嵌入和搜索配置的 KnowledgeBaseModel
`KnowledgeBaseModel` 应使用 `collection_name` 作为存储向量存储集合标识符的列，替换之前的 `qdrant_collection` 列。Alembic 迁移应重命名现有列。

#### Scenario: Default embedding model — 场景：默认嵌入模型
- **WHEN — 当** 创建知识库时
- **THEN — 则** `embedding_model` 应默认为 "BAAI/bge-m3"

#### Scenario: Search mode options — 场景：搜索模式选项
- **WHEN — 当** 设置 search_mode
- **THEN — 则** 应接受 "hybrid"（默认）、"dense" 或 "sparse"

#### Scenario: Collection name field — 场景：集合名称字段
- **WHEN — 当** 创建知识库并初始化向量存储集合时
- **THEN — 则** `collection_name` 应存储与后端无关的集合标识符

#### Scenario: CreateSchema uses collection_name — 场景：CreateSchema 使用 collection_name
- **WHEN — 当** 构造 `KnowledgeBaseCreateSchema`
- **THEN — 则** 集合字段应命名为 `collection_name`（而非 `qdrant_collection`）

#### Scenario: ReadSchema serializes collection_name — 场景：ReadSchema 序列化 collection_name
- **WHEN — 当** 序列化 `KnowledgeBaseReadSchema`
- **THEN — 则** 集合字段应在 JSON 输出中显示为 `collection_name`
