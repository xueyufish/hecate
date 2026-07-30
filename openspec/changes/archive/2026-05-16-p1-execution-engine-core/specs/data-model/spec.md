## ADDED Requirements — 新增需求

### Requirement: agents 表 — agents table

MUST create `agents` table with columns: `id` (UUID PK), `workspace_id` (UUID NOT NULL), `name` (VARCHAR(255)), `persona` (TEXT), `model_config` (JSONB NOT NULL), `mode` (VARCHAR(50), default `chat`), `workflow_id` (UUID), `tools` (JSONB), `skills` (JSONB), `knowledge_base_ids` (JSONB), `risk_level` (VARCHAR(20), default `LOW`), `created_at`/`updated_at`/`deleted_at`. MUST index `workspace_id`.

#### Scenario: 创建 Agent 并持久化 — Create and persist Agent
- **WHEN** 通过 API 创建一个 Agent，指定 name="客服助手"，mode="chat"，model_config={"model": "gpt-4o", "temperature": 0.7}
- **THEN** agents 表 MUST 插入一条记录，id 自动生成 UUID，created_at 和 updated_at 自动填充，deleted_at 为 NULL

#### Scenario: 软删除 Agent — Soft delete Agent
- **WHEN** 删除一个已存在的 Agent
- **THEN** 该记录 MUST NOT 从表中移除，而是将 `deleted_at` 设为当前时间戳

### Requirement: sessions 表 — sessions table

MUST create `sessions` table with columns including `conversation_id` (nullable, auto-create Conversation when NULL), `agent_id`, `status` (active/interrupted/completed/failed), `current_node`, `checkpoint_id`, `metadata` JSONB. In P1, Conversation and Session have 1:1 relationship.

#### Scenario: 创建 Session 并关联 Agent — Create Session linked to Agent
- **WHEN** 用户发起对话，创建一个 Session
- **THEN** sessions 表 MUST 插入记录，status 为 `active`，agent_id 指向目标 Agent

#### Scenario: Session 状态流转 — Session status transition
- **WHEN** 执行引擎中断 Session 执行
- **THEN** 该 Session 的 status MUST 更新为 `interrupted`，current_node 记录中断时的节点 ID

### Requirement: messages 表 — messages table

MUST create `messages` table with `conversation_id`, `role` (system/user/assistant/tool), `content`, `tool_calls` JSONB, `tool_call_id`, `metadata` JSONB. MUST composite index on `conversation_id` + `created_at`.

#### Scenario: 存储 LLM 带工具调用的回复 — Store LLM response with tool calls
- **WHEN** LLM 返回包含 tool_calls 的 assistant 消息
- **THEN** messages 表 MUST 插入记录，role="assistant"，content 为文本内容，tool_calls 为 JSON 数组

#### Scenario: 存储工具执行结果 — Store tool execution result
- **WHEN** 工具执行完成，结果需要回注到对话
- **THEN** messages 表 MUST 插入记录，role="tool"，content 为工具执行结果，tool_call_id 关联到对应的 tool_call

### Requirement: tools 表 — tools table

MUST create `tools` table with `source` (builtin/custom/mcp), `parameters` JSONB, `returns` JSONB, `risk_level`, `approval_required`, `mcp_server`, `mcp_tool_name`. MUST unique index on `workspace_id` + `name` (excluding soft-deleted).

#### Scenario: 注册 MCP 工具到 tools 表 — Register MCP tool in tools table
- **WHEN** MCP Server 发现一个名为 "web_search" 的工具
- **THEN** tools 表 MUST 插入记录，source="mcp"，mcp_server 为 MCP Server 标识

### Requirement: knowledge_bases 表 — knowledge_bases table

MUST create `knowledge_bases` table with `embedding_model`, `chunk_strategy`, `chunk_size`, `chunk_overlap`, `qdrant_collection`.

#### Scenario: 创建知识库时自动创建 Qdrant 集合 — Auto-create Qdrant collection when creating KB
- **WHEN** 创建名为 "产品文档" 的知识库
- **THEN** knowledge_bases 表 MUST 插入记录，qdrant_collection 自动生成

### Requirement: skills 表 — skills table

MUST create `skills` table with `source` (system/user/project), `instructions`, `allowed_tools`, `scripts`, `references`, `max_tokens`, `auto_load`. MUST unique index on `name`.

#### Scenario: 发现并注册项目级 Skill — Discover and register project-level Skill
- **WHEN** 系统扫描 `.skills/developer.md` 文件
- **THEN** skills 表 MUST 插入记录，source="project"

### Requirement: Pydantic v2 Schema 定义 — Pydantic v2 Schema Definition

Every table MUST have corresponding Pydantic v2 Models inheriting `BaseModel` with `ConfigDict(from_attributes=True)`. Each entity MUST define `CreateSchema` and `ReadSchema` variants.

### Requirement: conversations 表 — conversations table

MUST create `conversations` table with `agent_id`, `title`. P1: Conversation and Session have 1:1 relationship.

#### Scenario: 创建 Session 时自动创建 Conversation — Auto-create Conversation when creating Session
- **WHEN** 创建 Session 时未指定 conversation_id
- **THEN** 系统 MUST 自动创建 Conversation 记录并回填 ID

### Requirement: checkpoints 表 — checkpoints table

MUST create `checkpoints` table with `session_id`, `superstep` (increment from 1), `node_id`, `channel_state` JSONB, `pending_writes` JSONB, `metadata` JSONB. MUST composite index on `session_id` + `superstep`. Checkpoints MUST be immutable (INSERT only, no UPDATE/DELETE).

#### Scenario: 超步完成后写入 Checkpoint — Write Checkpoint after superstep completes
- **WHEN** superstep 3 执行节点 `"plan"` 完成
- **THEN** checkpoints 表 MUST 插入记录，superstep=3，channel_state 包含所有 Channel 当前值

#### Scenario: 从 Checkpoint 恢复执行 — Resume execution from Checkpoint
- **WHEN** Session 中断后用户请求恢复
- **THEN** 系统 MUST 从 checkpoints 表加载对应记录，重建 Channel 状态

#### Scenario: Checkpoint 不可变 — Checkpoint immutability
- **WHEN** 尝试 UPDATE 或 DELETE 一条已存在的 Checkpoint 记录
- **THEN** 数据库 MUST 拒绝该操作

### Requirement: documents 表 — documents table

MUST create `documents` table referencing `knowledge_bases`, with `filename`, `file_path` (MinIO path), `file_size`, `content_type`, `parsing_status` (pending/parsing/completed/failed), `parsing_error`, `chunk_count`.

#### Scenario: 上传文档并记录元数据 — Upload document and record metadata
- **WHEN** 用户上传文件 "产品手册.pdf"（2.5 MB）
- **THEN** documents 表 MUST 插入记录，parsing_status="pending"

#### Scenario: 文档解析状态流转 — Document parsing status flow
- **WHEN** Docling 开始解析文档
- **THEN** parsing_status → "parsing" → "completed" (set chunk_count) or "failed" (set parsing_error)

#### Scenario: 软删除文档 — Soft delete document
- **WHEN** 用户删除文档
- **THEN** 记录 MUST 设置 deleted_at，MinIO 文件和 Qdrant 向量 SHOULD 异步清理
