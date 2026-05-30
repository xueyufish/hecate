## Why

Hecate 的 RAG 检索目前只有稠密向量检索（3.2.1），依赖 Embedding 模型的语义相似度。这导致两个问题：

1. **精确关键词匹配缺失** — 用户搜索专有名词、错误码、API 名称等精确术语时，语义检索可能漏召回
2. **混合检索能力空白** — 无法结合语义理解和关键词匹配的优势，检索质量有明显上限

Qdrant 从 1.7+ 原生支持稀疏向量 + 内置 fusion API，我们已有 `qdrant-client>=1.12.0`，无需额外依赖即可实现。

## What Changes

- 新增 BM25 风格的关键词检索能力（稀疏向量索引 + 搜索）
- 新增混合检索能力（稠密 + 稀疏分数融合，支持加权配置）
- 让 `HybridSearcher` 从当前的"只做稠密检索"升级为真正的混合检索
- 让 `EnginePort.knowledge_query` 从 `raise NotImplementedError` 变为真正的 RAG 服务调用
- 知识库模型新增混合检索配置字段（权重、策略等）

## Capabilities

### New Capabilities

- `keyword-search`: BM25 风格稀疏向量检索 — 包括稀疏向量生成、Qdrant 稀疏向量集合管理、稀疏向量索引与搜索
- `hybrid-search`: 稠密 + 稀疏混合检索 — 包括分数融合策略（RRF / 加权）、检索配置管理、EnginePort 对接

### Modified Capabilities

- `context-assembler`: 知识检索结果需要注入到上下文组装流程中（通过 EnginePort.knowledge_query 接口）

## Impact

**代码变更：**
- `src/hecate/services/rag/embedding.py` — 新增稀疏向量生成（BGE-M3 sparse output）
- `src/hecate/services/rag/indexer.py` — Qdrant 集合创建支持稀疏向量配置
- `src/hecate/services/rag/searcher.py` — HybridSearcher 实现真正的混合检索逻辑
- `src/hecate/services/rag/service.py` — KnowledgeBaseService.search 支持混合参数
- `src/hecate/engine/ports.py` — knowledge_query 从 NotImplementedError 变为真实实现
- `src/hecate/models/knowledge.py` — KnowledgeBaseModel 新增混合检索配置字段
- `pyproject.toml` — 可选新增 `rank-bm25` 依赖（备选方案，优先用 Qdrant 原生）

**API 变更：**
- `POST /api/knowledge-bases/{id}/search` — 新增检索端点（可选，按需暴露）

**依赖：**
- 无新增必须依赖（Qdrant 原生方案）
- 可选：`rank-bm25` 作为备选 BM25 实现
