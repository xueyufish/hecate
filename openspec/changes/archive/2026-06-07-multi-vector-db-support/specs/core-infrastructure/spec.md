## MODIFIED Requirements — 修改的需求

### Requirement: Settings loaded from environment variables and .env file — 需求：从环境变量和 .env 文件加载设置
`Settings` 类（pydantic-settings）应包括 `VECTOR_STORE_TYPE`（默认 `"qdrant"`）和每后端连接设置：`QDRANT_URL`（默认 `"http://localhost:6333"`）、`QDRANT_API_KEY`（默认 `""`）和 `CHROMA_PERSIST_DIR`（默认 `"./data/chroma"`）。现有的 `QDRANT_URL` 字段应在 `VECTOR_STORE_TYPE=qdrant` 时保留以实现向后兼容。

#### Scenario: Default values — 场景：默认值
- **WHEN — 当** 未设置环境变量
- **THEN — 则** `Settings` 应使用默认值：`VECTOR_STORE_TYPE="qdrant"`、`QDRANT_URL="http://localhost:6333"`、`CHROMA_PERSIST_DIR="./data/chroma"`

#### Scenario: Qdrant configuration — 场景：Qdrant 配置
- **WHEN — 当** `VECTOR_STORE_TYPE=qdrant` 且 `QDRANT_URL=http://custom:6333`
- **THEN — 则** QdrantVectorStore 应连接到指定的 URL

#### Scenario: Chroma configuration — 场景：Chroma 配置
- **WHEN — 当** `VECTOR_STORE_TYPE=chroma` 且 `CHROMA_PERSIST_DIR=/data/vecs`
- **THEN — 则** ChromaVectorStore 应使用 `/data/vecs` 作为持久化目录

#### Scenario: Unsupported vector store type — 场景：不支持的向量存储类型
- **WHEN — 当** `VECTOR_STORE_TYPE` 设置为未识别的值
- **THEN — 则** `get_vector_store()` 应在运行时引发 `ValueError`
