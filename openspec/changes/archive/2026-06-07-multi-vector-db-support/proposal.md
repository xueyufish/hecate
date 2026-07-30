## Why — 为什么

RAG 管道与 Qdrant 紧密耦合——`QdrantIndexer` 是一个用作模块级单例的 monolithic 类，`KnowledgeBaseModel` 有一个 `qdrant_collection` 列，配置中硬编码了 `QDRANT_URL`。这阻止了支持替代向量数据库（轻量级开发用 Chroma，企业用 Milvus/Weaviate），违反了平台模型无关和基础设施可移植的设计原则。

## What Changes — 变更内容

- **新的 `VectorStore` ABC** 在 `services/rag/vector_store.py` 中定义了一个与后端无关的接口：`create_collection`、`delete_collection`、`collection_exists`、`upsert`、`delete_by_ids`、`search_dense`、`search_sparse`、`count`、`scroll`，加上可选的 `search_hybrid` 和默认的应用层 RRF 融合（4× 预取以缓解前缀采样偏差）
- **重构 `QdrantIndexer`** 为 `QdrantVectorStore` 实现 ABC——提取现有 Qdrant 逻辑，行为不变
- **新的 `ChromaVectorStore`** 作为轻量级、零依赖的开发环境后端
- **VectorStore 工厂**根据 `VECTOR_STORE_TYPE` 配置实例化正确的后端
- **重构 `HybridSearcher`** 调用 `VectorStore.search_hybrid()` 替代 Qdrant 原生融合——支持原生混合的后端（Qdrant、Milvus）覆盖该方法；不支持的后端（Chroma）继承默认的应用层 RRF 回退
- **破坏性变更：`KnowledgeBaseModel` 中的 `qdrant_collection` 列重命名为 `collection_name`**，通过 Alembic 迁移
- **配置变更**：`QDRANT_URL` 被 `VECTOR_STORE_TYPE`（全局类型选择器）+ 每后端环境变量（`QDRANT_URL`、`CHROMA_PERSIST_DIR` 等）替换，遵循 Dify 经过验证的模式

## Capabilities — 能力

### New Capabilities — 新能力
- `vector-store-abc`：定义向量存储接口的抽象基类，包含必需操作（稠密/稀疏搜索、集合 CRUD、upsert/delete）和可选的混合搜索及应用层 RRF 回退

### Modified Capabilities — 修改的能力
- `rag-pipeline`：`QdrantIndexer` 被 `VectorStore` ABC + 工厂替换；`KnowledgeBaseService` 使用抽象而非直接 Qdrant 调用；`reindex_with_sparse()` 封装修复
- `hybrid-search`：`HybridSearcher` 委托给 `VectorStore.search_hybrid()` 而非 Qdrant 原生融合；透明支持原生和回退融合
- `core-infrastructure`：新的 `VECTOR_STORE_TYPE` 配置字段和每后端连接设置替换单一的 `QDRANT_URL`
- `data-models`：`qdrant_collection` 列通过 Alembic 迁移重命名为 `collection_name`；更新了 Pydantic schemas

## Impact — 影响

- **代码**：`services/rag/indexer.py`（重大重构 → 拆分为 ABC + Qdrant 适配器）、`services/rag/searcher.py`（重构）、`services/rag/service.py`（重构为使用工厂）、`models/knowledge.py`（列重命名）、`core/config.py`（新配置字段）、`services/orchestration/agent_execution_port.py`（适配新接口）
- **数据库**：Alembic 迁移用于 `qdrant_collection` → `collection_name` 列重命名
- **API**：Pydantic schema 字段重命名（Create/Read schemas 中 `qdrant_collection` → `collection_name`）
- **依赖**：`chromadb` 添加到 `[rag]` 可选依赖组
- **配置**：用户必须设置 `VECTOR_STORE_TYPE=qdrant`（或 `chroma`）及相应的后端特定环境变量；类型为 qdrant 时 `QDRANT_URL` 仍然有效
- **测试**：引擎测试不受影响（无直接 Qdrant 依赖）；RAG 服务测试更新为针对 ABC 测试；新的 `ChromaVectorStore` 和工厂测试
