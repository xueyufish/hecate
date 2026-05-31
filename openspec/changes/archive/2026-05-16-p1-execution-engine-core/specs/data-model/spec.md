## ADDED Requirements

### Requirement: agents 表

系统 MUST 创建 `agents` 表，包含以下字段：`id`（UUID PRIMARY KEY）、`workspace_id`（UUID NOT NULL）、`name`（VARCHAR(255) NOT NULL）、`persona`（TEXT，系统提示词）、`model_config`（JSONB NOT NULL，包含主模型、备用模型、温度等参数）、`mode`（VARCHAR(50) NOT NULL，取值 `chat` | `three_layer` | `workflow`，默认 `chat`）、`workflow_id`（UUID NULLABLE，绑定的工作流）、`tools`（JSONB，关联工具列表）、`skills`（JSONB，关联 Skill 列表）、`knowledge_base_ids`（JSONB，关联知识库 ID 列表）、`risk_level`（VARCHAR(20)，默认 `LOW`）、`created_at`（TIMESTAMPTZ NOT NULL DEFAULT NOW()）、`updated_at`（TIMESTAMPTZ NOT NULL DEFAULT NOW()）、`deleted_at`（TIMESTAMPTZ NULLABLE）。MUST 在 `workspace_id` 上创建索引。

#### Scenario: 创建 Agent 并持久化
- **WHEN** 通过 API 创建一个 Agent，指定 name="客服助手"，mode="chat"，model_config={"model": "gpt-4o", "temperature": 0.7}
- **THEN** agents 表 MUST 插入一条记录，id 自动生成 UUID，created_at 和 updated_at 自动填充，deleted_at 为 NULL

#### Scenario: 软删除 Agent
- **WHEN** 删除一个已存在的 Agent
- **THEN** 该记录 MUST NOT 从表中移除，而是将 `deleted_at` 设为当前时间戳

### Requirement: sessions 表

系统 MUST 创建 `sessions` 表，包含以下字段：`id`（UUID PRIMARY KEY）、`conversation_id`（UUID NULLABLE REFERENCES conversations(id)，为 NULL 时自动创建 Conversation）、`agent_id`（UUID NOT NULL REFERENCES agents(id)）、`status`（VARCHAR(20) NOT NULL，取值 `active` | `interrupted` | `completed` | `failed`，默认 `active`）、`current_node`（VARCHAR(100) NULLABLE）、`checkpoint_id`（UUID NULLABLE，最新 Checkpoint ID）、`metadata`（JSONB DEFAULT '{}'）、`created_at`（TIMESTAMPTZ NOT NULL DEFAULT NOW()）、`updated_at`（TIMESTAMPTZ NOT NULL DEFAULT NOW()）。MUST 在 `agent_id` 和 `conversation_id` 上创建索引。P1 中 Conversation 与 Session 为 1:1 关系——一个 Session 对应一个 Conversation，创建 Session 时若 conversation_id 为 NULL 则自动创建 Conversation 并回填。

#### Scenario: 创建 Session 并关联 Agent
- **WHEN** 用户发起对话，创建一个 Session
- **THEN** sessions 表 MUST 插入记录，status 为 `active`，agent_id 指向目标 Agent

#### Scenario: Session 状态流转
- **WHEN** 执行引擎中断 Session 执行
- **THEN** 该 Session 的 status MUST 更新为 `interrupted`，current_node 记录中断时的节点 ID

### Requirement: messages 表

系统 MUST 创建 `messages` 表，包含以下字段：`id`（UUID PRIMARY KEY）、`conversation_id`（UUID NOT NULL）、`role`（VARCHAR(20) NOT NULL，取值 `system` | `user` | `assistant` | `tool`）、`content`（TEXT NOT NULL）、`tool_calls`（JSONB NULLABLE，工具调用列表）、`tool_call_id`（VARCHAR(100) NULLABLE，工具调用结果关联 ID）、`metadata`（JSONB DEFAULT '{}'，包含 Token 用量、模型、延迟等）。`created_at`（TIMESTAMPTZ NOT NULL DEFAULT NOW()）。MUST 在 `conversation_id` 和 `created_at` 上创建复合索引。

#### Scenario: 存储 LLM 带工具调用的回复
- **WHEN** LLM 返回包含 tool_calls 的 assistant 消息
- **THEN** messages 表 MUST 插入记录，role="assistant"，content 为文本内容，tool_calls 为 JSON 数组

#### Scenario: 存储工具执行结果
- **WHEN** 工具执行完成，结果需要回注到对话
- **THEN** messages 表 MUST 插入记录，role="tool"，content 为工具执行结果，tool_call_id 关联到对应的 tool_call

### Requirement: tools 表

系统 MUST 创建 `tools` 表，包含以下字段：`id`（UUID PRIMARY KEY）、`workspace_id`（UUID NOT NULL）、`name`（VARCHAR(255) NOT NULL）、`description`（TEXT NOT NULL）、`source`（VARCHAR(20) NOT NULL，取值 `builtin` | `custom` | `mcp`）、`parameters`（JSONB NOT NULL，JSON Schema 格式定义输入参数）、`returns`（JSONB，JSON Schema 格式定义输出）、`risk_level`（VARCHAR(20) DEFAULT 'LOW'）、`approval_required`（BOOLEAN DEFAULT FALSE）、`mcp_server`（VARCHAR(255) NULLABLE）、`mcp_tool_name`（VARCHAR(255) NULLABLE）、`created_at`（TIMESTAMPTZ NOT NULL DEFAULT NOW()）、`updated_at`（TIMESTAMPTZ NOT NULL DEFAULT NOW()）、`deleted_at`（TIMESTAMPTZ NULLABLE）。MUST 在 `workspace_id` 和 `name` 上创建唯一索引（不含已软删除记录）。

#### Scenario: 注册 MCP 工具到 tools 表
- **WHEN** MCP Server 发现一个名为 "web_search" 的工具
- **THEN** tools 表 MUST 插入记录，source="mcp"，mcp_server 为 MCP Server 标识，mcp_tool_name="web_search"，parameters 为工具的 JSON Schema

### Requirement: knowledge_bases 表

系统 MUST 创建 `knowledge_bases` 表，包含以下字段：`id`（UUID PRIMARY KEY）、`workspace_id`（UUID NOT NULL）、`name`（VARCHAR(255) NOT NULL）、`description`（TEXT NULLABLE）、`embedding_model`（VARCHAR(100) NOT NULL DEFAULT 'BAAI/bge-m3'）、`chunk_strategy`（VARCHAR(20) NOT NULL DEFAULT 'fixed'，取值 `auto` | `fixed` | `semantic`）、`chunk_size`（INTEGER NOT NULL DEFAULT 512）、`chunk_overlap`（INTEGER NOT NULL DEFAULT 100）、`qdrant_collection`（VARCHAR(255) NOT NULL，Qdrant 集合名）、`created_at`（TIMESTAMPTZ NOT NULL DEFAULT NOW()）、`updated_at`（TIMESTAMPTZ NOT NULL DEFAULT NOW()）、`deleted_at`（TIMESTAMPTZ NULLABLE）。

#### Scenario: 创建知识库时自动创建 Qdrant 集合
- **WHEN** 创建名为 "产品文档" 的知识库
- **THEN** knowledge_bases 表 MUST 插入记录，qdrant_collection 自动生成为 `"kb_{id}"`，同时在 Qdrant 中创建对应的混合索引集合

### Requirement: skills 表

系统 MUST 创建 `skills` 表，包含以下字段：`id`（UUID PRIMARY KEY）、`name`（VARCHAR(255) NOT NULL，小写字母+连字符）、`description`（TEXT NOT NULL）、`source`（VARCHAR(20) NOT NULL，取值 `system` | `user` | `project`）、`instructions`（TEXT NOT NULL，Skill 指令内容）、`allowed_tools`（JSONB DEFAULT '[]'）、`metadata`（JSONB DEFAULT '{}'）、`scripts`（JSONB DEFAULT '[]'）、`references`（JSONB DEFAULT '[]'）、`max_tokens`（INTEGER DEFAULT 2000）、`auto_load`（BOOLEAN DEFAULT FALSE）、`created_at`（TIMESTAMPTZ NOT NULL DEFAULT NOW()）、`updated_at`（TIMESTAMPTZ NOT NULL DEFAULT NOW()）、`deleted_at`（TIMESTAMPTZ NULLABLE）。MUST 在 `name` 上创建唯一索引。

#### Scenario: 发现并注册项目级 Skill
- **WHEN** 系统扫描 `.skills/developer.md` 文件
- **THEN** skills 表 MUST 插入记录，source="project"，name="developer"，instructions 为文件内容

### Requirement: Pydantic v2 Schema 定义

系统 MUST 为每张数据库表定义对应的 Pydantic v2 Model。所有 Model SHALL 继承 `BaseModel`，使用 `model_config = ConfigDict(from_attributes=True)` 支持 ORM 映射。UUID 字段 MUST 使用 `pydantic.UUID4` 类型。时间戳字段 MUST 使用 `datetime` 类型。JSONB 字段 MUST 使用对应的嵌套 Pydantic Model 或 `dict` 类型。每个实体 MUST 定义 `CreateSchema`（创建时必填字段）和 `ReadSchema`（完整读取字段，包含 id 和时间戳）两个变体。

#### Scenario: Agent CreateSchema 验证
- **WHEN** 使用 AgentCreateSchema 验证 `{"name": "助手", "model_config": {"model": "gpt-4o"}}`
- **THEN** Pydantic MUST 验证通过，自动填充 mode="chat" 默认值

#### Scenario: 缺少必需字段验证失败
- **WHEN** 使用 AgentCreateSchema 验证 `{"name": "助手"}`
- **THEN** Pydantic MUST 抛出 `ValidationError`，提示 `model_config` 字段缺失

### Requirement: conversations 表

系统 MUST 创建 `conversations` 表，包含以下字段：`id`（UUID PRIMARY KEY）、`agent_id`（UUID NOT NULL REFERENCES agents(id)）、`title`（VARCHAR(255) NULLABLE，对话标题）、`created_at`（TIMESTAMPTZ NOT NULL DEFAULT NOW()）、`updated_at`（TIMESTAMPTZ NOT NULL DEFAULT NOW()）、`deleted_at`（TIMESTAMPTZ NULLABLE）。MUST 在 `agent_id` 上创建索引。P1 中 Conversation 与 Session 为 1:1 关系。

#### Scenario: 创建 Session 时自动创建 Conversation
- **WHEN** 创建 Session 时未指定 conversation_id（为 NULL）
- **THEN** 系统 MUST 自动创建一条 Conversation 记录，agent_id 与 Session 一致，title 默认为 NULL，并将新 Conversation 的 id 回填到 Session 的 conversation_id 字段

#### Scenario: 对话标题自动生成
- **WHEN** Conversation 关联的第一条用户消息被处理
- **THEN** 系统 MAY 基于 LLM 生成摘要作为 title，或保持 NULL 待前端展示时动态生成

### Requirement: checkpoints 表

系统 MUST 创建 `checkpoints` 表，包含以下字段：`id`（UUID PRIMARY KEY）、`session_id`（UUID NOT NULL REFERENCES sessions(id)）、`superstep`（INTEGER NOT NULL，超步编号，从 1 开始递增）、`node_id`（VARCHAR(100) NULLABLE，当前执行的节点 ID）、`channel_state`（JSONB NOT NULL DEFAULT '{}'，所有 Channel 当前值序列化）、`pending_writes`（JSONB DEFAULT '[]'，待写入的 Channel 更新列表）、`metadata`（JSONB DEFAULT '{}'，执行元数据如耗时、Token 用量等）、`created_at`（TIMESTAMPTZ NOT NULL DEFAULT NOW()）。MUST 在 `session_id` 和 `superstep` 上创建复合索引。Checkpoint 一旦写入 MUST 不可修改（仅 INSERT，无 UPDATE/DELETE），支持时间旅行调试。

#### Scenario: 超步完成后写入 Checkpoint
- **WHEN** superstep 3（执行节点 `"plan"`）完成
- **THEN** checkpoints 表 MUST 插入一条记录，superstep=3，node_id="plan"，channel_state 包含所有 Channel 当前值

#### Scenario: 从 Checkpoint 恢复执行
- **WHEN** Session 中断后用户请求恢复，提供 checkpoint_id
- **THEN** 系统 MUST 从 checkpoints 表加载对应记录，重建 Channel 状态，从断点的下一个超步继续 Pregel 循环

#### Scenario: Checkpoint 不可变
- **WHEN** 尝试 UPDATE 或 DELETE 一条已存在的 Checkpoint 记录
- **THEN** 数据库 MUST 拒绝该操作（通过数据库权限或应用层校验）

### Requirement: documents 表

系统 MUST 创建 `documents` 表，包含以下字段：`id`（UUID PRIMARY KEY）、`knowledge_base_id`（UUID NOT NULL REFERENCES knowledge_bases(id)）、`filename`（VARCHAR(255) NOT NULL，原始文件名）、`file_path`（TEXT NOT NULL，MinIO 存储路径）、`file_size`（BIGINT DEFAULT 0，文件大小字节数）、`content_type`（VARCHAR(100) NULLABLE，MIME 类型）、`parsing_status`（VARCHAR(20) NOT NULL DEFAULT 'pending'，取值 `pending` | `parsing` | `completed` | `failed`）、`parsing_error`（TEXT NULLABLE，解析失败时的错误信息）、`chunk_count`（INTEGER DEFAULT 0，解析后生成的分块数量）、`created_at`（TIMESTAMPTZ NOT NULL DEFAULT NOW()）、`updated_at`（TIMESTAMPTZ NOT NULL DEFAULT NOW()）、`deleted_at`（TIMESTAMPTZ NULLABLE）。MUST 在 `knowledge_base_id` 上创建索引。

#### Scenario: 上传文档并记录元数据
- **WHEN** 用户向知识库上传文件 "产品手册.pdf"（2.5 MB）
- **THEN** documents 表 MUST 插入一条记录，filename="产品手册.pdf"，file_path 为 MinIO 路径（如 `"kb/{kb_id}/产品手册.pdf"`），file_size=2621440，content_type="application/pdf"，parsing_status="pending"

#### Scenario: 文档解析状态流转
- **WHEN** Docling 开始解析文档
- **THEN** parsing_status MUST 更新为 `"parsing"`；解析完成后更新为 `"completed"` 并设置 chunk_count；解析失败时更新为 `"failed"` 并设置 parsing_error

#### Scenario: 软删除文档
- **WHEN** 用户从知识库中删除一个文档
- **THEN** 该记录 MUST NOT 从表中移除，而是将 `deleted_at` 设为当前时间戳；对应的 MinIO 文件和 Qdrant 向量 SHOULD 在后台异步清理
