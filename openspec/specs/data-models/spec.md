## ADDED Requirements

### Requirement: BaseModel provides UUID primary key, timestamps, and soft delete
The abstract `BaseModel` SHALL provide `id` (UUID4), `created_at`, `updated_at`, and `deleted_at` columns for all concrete ORM models.

#### Scenario: UUID primary key auto-generated
- **WHEN** a new model instance is created
- **THEN** `id` SHALL be auto-generated via `uuid.uuid4`

#### Scenario: Timestamps set by database server
- **WHEN** a row is inserted
- **THEN** `created_at` and `updated_at` SHALL be set by `server_default=func.now()`

#### Scenario: Updated_at refreshed on UPDATE
- **WHEN** a row is updated
- **THEN** `updated_at` SHALL be refreshed via `onupdate=func.now()`

#### Scenario: Soft delete via deleted_at
- **WHEN** a row's `deleted_at` is set to a datetime
- **THEN** the row SHALL still exist in the database but be excluded from queries via `WHERE deleted_at IS NULL`

### Requirement: AgentModel with model_config column alias
The `AgentModel` SHALL use `model_config_db` as the Python attribute name mapping to the `model_config` database column to avoid collision with Pydantic's reserved `model_config`.

#### Scenario: CreateSchema uses alias for model_config
- **WHEN** `AgentCreateSchema` is constructed with `model_config={...}`
- **THEN** the field SHALL be aliased from `"model_config"` to `llm_config` via `Field(alias="model_config")`

#### Scenario: ReadSchema serializes with alias
- **WHEN** `AgentReadSchema` is serialized
- **THEN** `model_config_db` SHALL be serialized as `"model_config"` via `serialization_alias="model_config"`

### Requirement: Agent execution modes
The `AgentModel.mode` field SHALL accept "chat", "three_layer", or "workflow" values.

#### Scenario: Chat mode
- **WHEN** mode is "chat"
- **THEN** the agent SHALL use single-LLM conversation mode

#### Scenario: Three-layer mode
- **WHEN** mode is "three_layer"
- **THEN** the agent SHALL use the Guard→Planner→Sub-Agent template

#### Scenario: Workflow mode
- **WHEN** mode is "workflow" and `workflow_id` is set
- **THEN** the agent SHALL execute the referenced workflow graph

### Requirement: SessionModel with metadata_ column alias
The `SessionModel` SHALL use `metadata_` as the Python attribute mapping to the `metadata` database column to avoid collision with SQLAlchemy's reserved `metadata`.

#### Scenario: Session status lifecycle
- **WHEN** a session is created
- **THEN** status SHALL default to "active"

#### Scenario: Session interrupted
- **WHEN** execution hits an interrupt point
- **THEN** status SHALL be set to "interrupted" and `current_node` SHALL record the paused node

### Requirement: MessageModel with tool_calls JSONB
The `MessageModel` SHALL store tool call descriptors in a JSONB column following OpenAI's tool_calls format.

#### Scenario: Assistant message with tool calls
- **WHEN** an assistant invokes tools
- **THEN** `tool_calls` SHALL contain `[{"id": "call_xxx", "function": {"name": "...", "arguments": "..."}}]`

#### Scenario: Tool result message
- **WHEN** a tool result is stored
- **THEN** `role` SHALL be "tool" and `tool_call_id` SHALL reference the corresponding call ID

### Requirement: ToolModel with multi-source tools
The `ToolModel` SHALL support "builtin", "custom", and "mcp" source types.

#### Scenario: MCP tool
- **WHEN** source is "mcp"
- **THEN** `mcp_server` and `mcp_tool_name` SHALL identify the originating MCP server and tool

#### Scenario: Unique name per workspace
- **WHEN** a tool is created
- **THEN** the combination of (workspace_id, name) SHALL be unique among non-deleted tools

### Requirement: KnowledgeBaseModel with embedding and search config
The `KnowledgeBaseModel` SHALL store embedding model, chunking strategy, and search mode configuration.

#### Scenario: Default embedding model
- **WHEN** a knowledge base is created
- **THEN** `embedding_model` SHALL default to "BAAI/bge-m3"

#### Scenario: Search mode options
- **WHEN** search_mode is set
- **THEN** it SHALL accept "hybrid" (default), "dense", or "sparse"

### Requirement: DocumentModel with parsing status state machine
The `DocumentModel` SHALL track document processing through: "pending" → "parsing" → "completed"/"failed".

#### Scenario: Upload creates pending document
- **WHEN** a document is uploaded
- **THEN** `parsing_status` SHALL be "pending" and `chunk_count` SHALL be 0

#### Scenario: Parsing completed
- **WHEN** parsing succeeds
- **THEN** `parsing_status` SHALL be "completed" and `chunk_count` SHALL be set to the actual count

#### Scenario: Parsing failed
- **WHEN** parsing fails
- **THEN** `parsing_status` SHALL be "failed" and `parsing_error` SHALL contain the error message

### Requirement: CheckpointModel is immutable
The `CheckpointModel` SHALL extend `Base` directly (not `BaseModel`) and have no `updated_at` or `deleted_at` columns.

#### Scenario: Checkpoint created with state
- **WHEN** a checkpoint is saved
- **THEN** it SHALL store `session_id`, `superstep`, `node_id`, `channel_state` (JSONB), `pending_writes` (JSONB), and `metadata_` (JSONB)

#### Scenario: Checkpoint never updated
- **WHEN** a checkpoint is written
- **THEN** it SHALL never be modified or deleted (append-only)

### Requirement: SkillModel with lowercase name constraint
The `SkillModel` SHALL enforce lowercase hyphenated names matching pattern `^[a-z][a-z0-9-]*$`.

#### Scenario: Valid skill name
- **WHEN** a skill is created with name "developer"
- **THEN** it SHALL be accepted

#### Scenario: Unique name globally
- **WHEN** a skill is created
- **THEN** the name SHALL be unique among non-deleted skills (global, not per-workspace)
