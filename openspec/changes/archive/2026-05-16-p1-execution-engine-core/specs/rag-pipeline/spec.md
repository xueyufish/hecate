## ADDED Requirements

### Requirement: 文档上传与解析

系统 MUST 支持文档上传接口，接收 PDF、DOCX、Markdown、HTML 等格式的文件。上传后系统 SHALL 使用 Docling 库解析文档，提取纯文本内容。文档解析 MUST 异步执行，上传接口立即返回文档记录（parsing_status="pending"）。解析状态 MUST 通过 `GET /api/knowledge-bases/{id}/documents/{doc_id}` 查询，取值为 `pending` | `parsing` | `completed` | `failed`。

#### Scenario: 上传 PDF 文件并触发异步解析
- **WHEN** 通过 `POST /api/knowledge-bases/{id}/documents` 上传一个 10 页的 PDF 文件
- **THEN** 接口 MUST 立即返回文档记录（parsing_status="pending"），后台启动 Docling 解析，解析完成后 status 更新为 "completed"

#### Scenario: 解析失败的错误记录
- **WHEN** 上传的文件为加密 PDF，Docling 无法解析
- **THEN** 文档记录的 parsing_status MUST 更新为 "failed"，并记录错误信息

### Requirement: 文本分片

系统 MUST 将解析后的文本按固定大小分片（fixed chunking）。默认分片大小 MUST 为 512-1024 tokens，重叠区间 MUST 为 100-200 tokens。分片参数 SHALL 可在知识库级别配置（`chunk_size` 和 `chunk_overlap`）。每个分片 MUST 保留元数据（来源文档 ID、页码、在文档中的位置偏移）。

#### Scenario: 按配置参数分片
- **WHEN** 解析后的文本为 5000 tokens，知识库配置 chunk_size=512，chunk_overlap=100
- **THEN** 系统 MUST 生成约 12 个分片（5000 / (512-100) ≈ 12），每个分片约 512 tokens，相邻分片重叠约 100 tokens

#### Scenario: 分片保留元数据
- **WHEN** 对一份 10 页 PDF 的第 3 页内容生成分片
- **THEN** 分片的 metadata MUST 包含 `{"document_id": "xxx", "page": 3, "offset": 1500}`

### Requirement: BGE-M3 Embedding 编码

系统 MUST 使用 BGE-M3 模型（BAAI/bge-m3）对文本分片进行编码。每个分片 MUST 生成 dense 向量（1024 维）和 sparse 向量（BM25 权重）。P1 阶段 dense 向量 SHALL 为默认使用模式，sparse 向量 MUST 同时生成用于混合检索。编码过程 MUST 支持批量处理（batch size 可配置）。

#### Scenario: 单个分片生成 dense + sparse 向量
- **WHEN** 对一个文本分片调用 BGE-M3 encode
- **THEN** MUST 返回 dense 向量（List[float]，长度 1024）和 sparse 向量（Dict[str, float]，词到权重的映射）

#### Scenario: 批量编码提升效率
- **WHEN** 知识库包含 500 个分片需要编码
- **THEN** 系统 MUST 按 batch_size=32 分批编码，而不是逐个处理

### Requirement: Qdrant 混合索引创建

系统 MUST 将编码后的分片写入 Qdrant 向量数据库。每个知识库 MUST 对应一个 Qdrant Collection。Collection MUST 配置 dense 向量索引（1024 维，Cosine 相似度）和 sparse 向量索引（BM25）。分片写入时 MUST 同时存储 dense 向量、sparse 向量、原始文本内容和元数据。

#### Scenario: 创建知识库时初始化 Qdrant Collection
- **WHEN** 创建名为 "产品文档" 的知识库
- **THEN** 系统 MUST 在 Qdrant 中创建 Collection，配置 dense 向量（dim=1024, metric=Cosine）和 sparse 向量索引

#### Scenario: 分片写入 Qdrant
- **WHEN** 一个分片完成 BGE-M3 编码
- **THEN** 系统 MUST 将该分片的 dense 向量、sparse 向量、content、metadata 作为一条记录写入 Qdrant Collection

### Requirement: Hybrid Search 混合检索

系统 MUST 实现基于 Qdrant 的混合检索（Hybrid Search）。检索时 MUST 同时使用 dense 向量和 sparse 向量进行查询，通过 RRF（Reciprocal Rank Fusion）或加权融合合并结果。用户查询 MUST 通过 BGE-M3 编码为 dense + sparse 向量。系统 MUST 支持指定 top-K 参数，默认返回 top-5 结果。

#### Scenario: 混合检索返回相关结果
- **WHEN** 用户查询 "Hecate 的架构设计"，指定知识库 ID，top-K=5
- **THEN** 系统 MUST 将查询编码为 dense+sparse 向量，在 Qdrant 中执行混合检索，返回 5 个最相关的分片，每个分片包含 content、metadata 和 relevance_score

#### Scenario: 指定多知识库联合检索
- **WHEN** 用户查询指定了 2 个知识库 ID
- **THEN** 系统 MUST 在两个知识库对应的 Qdrant Collection 中执行检索，合并排序后返回 top-K 结果

### Requirement: 知识库 CRUD 管理

系统 MUST 支持知识库的创建、读取、更新、删除操作。创建知识库时 MUST 指定 name 和可选的 embedding_model（默认 BAAI/bge-m3）、chunk_size（默认 512）、chunk_overlap（默认 100）。删除知识库时 MUST 软删除数据库记录，并 MUST 删除对应的 Qdrant Collection。

#### Scenario: 创建自定义配置的知识库
- **WHEN** 创建知识库，指定 chunk_size=1024，chunk_overlap=200
- **THEN** 知识库记录 MUST 保存配置，后续文档上传使用该配置进行分片

#### Scenario: 删除知识库清理 Qdrant Collection
- **WHEN** 删除一个知识库
- **THEN** 数据库记录 MUST 软删除（设置 deleted_at），Qdrant 中对应的 Collection MUST 被删除
