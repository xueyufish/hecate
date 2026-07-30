## Context — 上下文

Hecate 的 RAG 管道（`services/rag/`）通过 `QdrantIndexer` 与 Qdrant 紧密耦合——这是一个位于 `indexer.py` 的 monolithic 类，同时充当向量存储接口和 Qdrant 客户端包装器。它被实例化为模块级单例（`qdrant_indexer = QdrantIndexer()`）并被 `HybridSearcher`、`KnowledgeBaseService` 以及间接地被 `AgentExecutionPort` 直接使用。`KnowledgeBaseModel` 有一个 `qdrant_collection` 列，将数据模型绑定到特定后端。配置（`core/config.py`）硬编码了 `QDRANT_URL`。

五层架构（AD-2）将 RAG 放在能力服务层——`services/rag/` 依赖于外部库和基础设施，这是可插拔向量存储抽象的正确层次。

## Goals / Non-Goals — 目标 / 非目标

**Goals — 目标：**
- 定义一个 `VectorStore` ABC，将 RAG 操作与任何特定向量数据库解耦
- 将现有 Qdrant 实现迁移到 `QdrantVectorStore` 适配器（零行为变更）
- 添加 `ChromaVectorStore` 作为轻量级开发后端
- 透明地支持混合搜索：支持原生混合的后端（Qdrant、Milvus）使用它；其他后端回退到应用层 RRF 融合
- 将 `qdrant_collection` 重命名为 `collection_name` 以实现后端无关的数据模型
- 引入 `VECTOR_STORE_TYPE` 配置模式，遵循 Dify 经过验证的方法

**Non-Goals — 非目标：**
- Milvus 和 Weaviate 适配器（P2/P3，未来的变更）
- 每集合后端选择（仅全局后端）
- 将 `PostgresCheckpointStore` 从 `engine/` 移动到 `services/`（记为 D8，单独的变更）
- 更改 `EmbeddingService` 接口或 BGE-M3 模型
- 修改 `EnginePort.knowledge_query()` 抽象接口

## Decisions — 决策

### D1: VectorStore ABC 位置——`services/rag/vector_store.py`

**Choice — 选择**：将 ABC 放在 `services/rag/` 中，而非 `engine/`。

**Rationale — 理由**：VectorStore 是能力服务层的关注点（RAG），而非执行引擎的概念。引擎层零外部依赖；向量存储客户端（qdrant-client、chromadb）是外部库。这遵循 AD-2 五层架构：VectorStore 位于能力服务层，与 `EmbeddingService` 和 `KnowledgeBaseService` 相同。

**Alternatives considered — 考虑的替代方案**：`engine/ports.py`——被拒绝，因为 `EnginePort` 已经将 `knowledge_query()` 定义为抽象委托点，而向量存储是该端口后面的实现细节，而非引擎抽象。

### D2: 混合搜索方法——"A+" 可选原生 + 应用层回退

**Choice — 选择**：ABC 定义 `search_dense()` + `search_sparse()`（必需抽象）+ `search_hybrid()`（可选，默认实现使用应用层 RRF 融合，4× 预取）。

```python
class VectorStore(ABC):
    @abstractmethod
    async def search_dense(self, ...) -> list[SearchResult]: ...

    @abstractmethod
    async def search_sparse(self, ...) -> list[SearchResult]: ...

    @property
    def supports_hybrid(self) -> bool:
        return False  # 在支持原生混合的后端中覆盖

    async def search_hybrid(self, dense_query, sparse_query, ..., top_k: int) -> list[SearchResult]:
        # 默认：应用层 RRF 带 4× 预取
        dense = await self.search_dense(dense_query, top_k=top_k * 4)
        sparse = await self.search_sparse(sparse_query, top_k=top_k * 4)
        return _rrf_fuse(dense, sparse, k=60, top_k=top_k)
```

**Rationale — 理由**：对 6 个平台（Dify、LlamaIndex、RAGFlow、Bisheng、Langflow、AgentScope）的研究确认，所有平台都将混合逻辑保持在 ABC 之外。我们的 A+ 方法更进一步，提供了一个可工作的默认实现——没有原生混合的后端（Chroma）获得自动回退，而 Qdrant/Milvus 覆盖以保持零质量损失。4× 预取缓解了前缀采样偏差（一个在稠密中排名 #15、在稀疏中排名 #12 的文档在 K=10 时不可见，但可能是融合赢家）。

**Alternatives considered — 考虑的替代方案**：
- A（纯应用层）：在支持原生混合的后端上损失质量
- B（ABC 将混合定义为抽象）：强制所有后端实现混合，对 Chroma 不切实际

### D3: RRF k 常数——60（标准）

**Choice — 选择**：在应用层 RRF 中使用 k=60，符合 Cormack 等人（2009）论文的经验推荐。

**Rationale — 理由**：Qdrant 使用 k=2（非标准，更激进的排名区分）。在默认融合中使用 k=60 提供了一致的、经过充分研究的行为。当后端用原生融合（如 Qdrant）覆盖 `search_hybrid()` 时，它们在内部使用自己的 k——ABC 不对原生路径规定 k。

### D4: 列重命名——`qdrant_collection` → `collection_name`

**Choice — 选择**：Alembic 迁移来重命名列，更新所有引用。

**Rationale — 理由**：保留 `qdrant_` 前缀违反变更的核心目标（后端解耦）。项目早期（数据量极小），迁移成本可以忽略不计。

### D5: 配置模式——`VECTOR_STORE_TYPE` + 每后端环境变量

**Choice — 选择**：分离类型选择器和每后端连接配置，匹配 Dify 经过验证的模式：
```bash
VECTOR_STORE_TYPE=qdrant    # 全局类型选择器
QDRANT_URL=http://...       # Qdrant 特定（type=qdrant 时）
QDRANT_API_KEY=             # 可选
CHROMA_PERSIST_DIR=./data   # Chroma 特定（type=chroma 时）
```

**Rationale — 理由**：与 `DATABASE_URL` 不同（SQLAlchemy 提供通用连接格式），向量数据库具有根本不同的连接模型（HTTP、gRPC、本地文件路径）。对于像 Chroma 这样的嵌入式后端，单一的 URL scheme 会显得不自然（`chroma:///path/to/data`）。Dify（VECTOR_STORE）和 RAGFlow（DOC_ENGINE）都在生产中使用了这种模式。

### D6: 适配器实例化——工厂函数

**Choice — 选择**：`services/rag/factory.py` 中的 `get_vector_store()` 工厂函数，读取配置并返回正确的 `VectorStore` 实例。

```python
def get_vector_store() -> VectorStore:
    match settings.vector_store_type:
        case "qdrant": return QdrantVectorStore(url=settings.qdrant_url, ...)
        case "chroma": return ChromaVectorStore(persist_dir=settings.chroma_persist_dir, ...)
        case _: raise ValueError(f"不支持的 VECTOR_STORE_TYPE: {settings.vector_store_type}")
```

**Rationale — 理由**：简单的 match/case 工厂。无需依赖注入框架——遵循与 `QdrantIndexer` 的惰性初始化相同的模式，但使用基于类型的选择。用返回正确适配器的函数替换模块级单例。

### D7: SearchResult 类型——`services/rag/types.py` 中的共享 dataclass

**Choice — 选择**：将 `SearchResult` 从 `indexer.py` 移动到 `types.py`（已存在 `Citation`）。这成为所有向量存储后端的共享返回类型。

**Rationale — 理由**：目前 `SearchResult` 在 `indexer.py` 中定义。当 ABC 在单独的文件中时，返回类型必须能被 ABC 和所有适配器导入，且无循环依赖。

## Risks / Trade-offs — 风险 / 权衡

**[应用层 RRF 质量差距]** → 通过 4× 预取缓解。对于 K=10 的最终结果，我们从每个通道获取 40 个。经验研究表明这保留了 >95% 的融合候选。使用 Qdrant/Milvus 的企业部署通过覆盖获得原生融合，零差距。

**[破坏性 API 变更：`qdrant_collection` → `collection_name`]** → Alembic 迁移处理数据库。Pydantic schema 字段重命名对于任何外部消费者来说都是破坏性 API 变更。通过项目早期阶段（有限的外部 API 使用）缓解。在变更日志中记录。

**[Chroma 性能]** → Chroma 是纯 Python，无原生 ANN 索引。仅用于开发/小数据集。生产部署使用 Qdrant/Milvus。配置文档将明确说明这一点。

**[模拟回退一致性]** → 当前的 `QdrantIndexer` 在未安装 `qdrant-client` 时有模拟回退。此模式必须在 `QdrantVectorStore` 中保留。`ChromaVectorStore` 同样需要为测试环境提供模拟回退。工厂必须优雅地处理缺失的可选依赖。

## Migration Plan — 迁移计划

1. **阶段 1 —— ABC + Qdrant 适配器**：创建 `VectorStore` ABC，将 `QdrantIndexer` 重构为 `QdrantVectorStore`，更新工厂。所有现有测试通过（无行为变更）。
2. **阶段 2 —— 列重命名**：Alembic 迁移 `qdrant_collection` → `collection_name`。更新模型、schemas 和所有引用。
3. **阶段 3 —— 配置更新**：向 Settings 添加 `VECTOR_STORE_TYPE`，更新工厂，更新 `.env.example`。
4. **阶段 4 —— Chroma 适配器**：新的 `ChromaVectorStore` 带模拟回退。适配器的测试。
5. **阶段 5 —— 更新消费者**：重构 `HybridSearcher`、`KnowledgeBaseService`、`AgentExecutionPort` 以使用工厂替代 `qdrant_indexer` 单例。

**Rollback — 回滚**：每个阶段都可以独立提交。如果 Chroma 适配器有问题，阶段 1-4 不受影响。列重命名迁移有对应的降级。
