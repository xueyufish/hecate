## ADDED Requirements — 新增需求

### Requirement: 文档上传与解析 — Document Upload and Parsing

系统 MUST 支持上传 PDF/DOCX/Markdown/HTML 等格式，通过 Docling 异步解析。上传立即返回（parsing_status="pending"），状态可查询。
— System MUST support uploading documents, async parsing via Docling, status queryable.

#### Scenario: 上传 PDF 文件并触发异步解析 — Upload PDF triggers async parsing
- **WHEN** 上传 10 页 PDF
- **THEN** 接口立即返回，后台 Docling 解析更新状态

### Requirement: 文本分片 — Text Chunking

系统 MUST 将解析后的文本按固定大小分片。默认 512-1024 tokens，overlap 100-200 tokens。参数可配置。每个分片保留元数据（文档 ID、页码、偏移）。
— System MUST chunk parsed text with configurable size/overlap, preserve metadata.

#### Scenario: 按配置参数分片 — Chunk by configured parameters
- **WHEN** 5000 tokens，chunk_size=512，overlap=100
- **THEN** 生成约 12 个分片

### Requirement: BGE-M3 Embedding 编码 — BGE-M3 Embedding

系统 MUST 使用 BGE-M3 生成 dense (1024-dim) 和 sparse (BM25 weights) 向量。支持批量编码。
— System MUST use BGE-M3 for dense + sparse encoding with batch support.

### Requirement: Qdrant 混合索引创建 — Qdrant Hybrid Index Creation

每个知识库对应一个 Qdrant Collection（dense + sparse 双向量索引）。存储向量、文本和元数据。
— Each KB maps to a Qdrant Collection with dual vector index.

### Requirement: Hybrid Search 混合检索 — Hybrid Search

系统 MUST 实现基于 Qdrant 的混合检索（dense + sparse），通过 RRF 或加权融合。默认返回 top-5。
— System MUST implement hybrid search via Qdrant with RRF fusion. Default top-5.

#### Scenario: 指定多知识库联合检索 — Multi-KB joint search
- **WHEN** 查询指定 2 个知识库
- **THEN** 系统 MUST 在两个 Collection 检索后合并排序

### Requirement: 知识库 CRUD 管理 — Knowledge Base CRUD

支持知识库创建/读取/更新/删除。删除时软删除记录并清理 Qdrant Collection。
— Support KB CRUD. Soft delete records and clean up Qdrant Collection on delete.

#### Scenario: 删除知识库清理 Qdrant Collection — Delete KB cleans Qdrant Collection
- **WHEN** 删除一个知识库
- **THEN** 数据库记录 MUST 软删除，Qdrant Collection MUST 被删除
