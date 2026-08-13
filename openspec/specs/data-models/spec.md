## Purpose

Data models define the SQLAlchemy ORM schema for the Hecate platform, including abstract base models with UUID primary keys, timestamp and soft-delete support, and concrete models for agents, sessions, messages, tools, knowledge bases, documents, checkpoints, and skills — with careful alias handling for columns that collide with Pydantic or SQLAlchemy reserved names.
## Requirements
### Requirement: BaseModel provides UUID primary key, timestamps, and soft delete
The abstract `BaseModel` SHALL provide `id` (UUID4), `created_at`, `updated_at`, `deleted` (bool), and `deleted_at` columns for all concrete ORM models. The `deleted` field represents the deletion state; the `deleted_at` field is an audit timestamp recording when deletion occurred.

#### Scenario: UUID primary key auto-generated
- **WHEN** a new model instance is created
- **THEN** `id` SHALL be auto-generated via `uuid.uuid4`

#### Scenario: Timestamps set by database server
- **WHEN** a row is inserted
- **THEN** `created_at` and `updated_at` SHALL be set by `server_default=func.now()`

#### Scenario: Updated_at refreshed on UPDATE
- **WHEN** a row is updated
- **THEN** `updated_at` SHALL be refreshed via `onupdate=func.now()`

#### Scenario: New row is not deleted by default
- **WHEN** a new model instance is created
- **THEN** `deleted` SHALL be `False` and `deleted_at` SHALL be `None`

#### Scenario: Soft delete sets both deleted and deleted_at
- **WHEN** a row is soft-deleted
- **THEN** `deleted` SHALL be set to `True` and `deleted_at` SHALL be set to the current timestamp

#### Scenario: Active rows queried by deleted field
- **WHEN** queries filter for active (non-deleted) rows
- **THEN** they SHALL use `WHERE deleted = false` (not `WHERE deleted_at IS NULL`)

#### Scenario: Unique composite indexes include deleted field
- **WHEN** a unique index enforces name uniqueness among active rows
- **THEN** the index SHALL be `Index("name", <columns...>, "deleted", "deleted_at", unique=True)` — fully portable across PostgreSQL, MySQL, and SQLite

#### Scenario: Non-unique filtered indexes include deleted field
- **WHEN** a non-unique index previously used `postgresql_where=deleted_at IS NULL`
- **THEN** the index SHALL be `Index("name", <columns...>, "deleted")` — composite index without dialect-specific kwargs

#### Scenario: Tenant-scoped models have workspace_id FK
- **WHEN** a resource model that belongs to a tenant is defined
- **THEN** it SHALL have a `workspace_id` UUID column with FK to `WorkspaceModel.id`, a composite index `idx_<table>_workspace` on `(workspace_id, deleted)`, and a server default of zero UUID

#### Scenario: Tenant-scoped models filter by workspace_id
- **WHEN** service-layer queries are executed against a tenant-scoped model
- **THEN** queries SHALL include `WHERE workspace_id = :workspace_id` as a mandatory filter condition

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
The `KnowledgeBaseModel` SHALL use `collection_name` as the column storing the vector store collection identifier, replacing the previous `qdrant_collection` column. An Alembic migration SHALL rename the existing column.

#### Scenario: Default embedding model
- **WHEN** a knowledge base is created
- **THEN** `embedding_model` SHALL default to "BAAI/bge-m3"

#### Scenario: Search mode options
- **WHEN** search_mode is set
- **THEN** it SHALL accept "hybrid" (default), "dense", or "sparse"

#### Scenario: Collection name field
- **WHEN** a knowledge base is created and a vector store collection is initialized
- **THEN** `collection_name` SHALL store the backend-agnostic collection identifier

#### Scenario: CreateSchema uses collection_name
- **WHEN** `KnowledgeBaseCreateSchema` is constructed
- **THEN** the collection field SHALL be named `collection_name` (not `qdrant_collection`)

#### Scenario: ReadSchema serializes collection_name
- **WHEN** `KnowledgeBaseReadSchema` is serialized
- **THEN** the collection field SHALL appear as `collection_name` in the JSON output

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

### Requirement: SkillModel with lowercase name constraint and workspace isolation
The `SkillModel` SHALL enforce lowercase hyphenated names matching pattern `^[a-z][a-z0-9-]*$`, and SHALL include a `workspace_id` column of type UUID defaulting to the zero UUID. The unique index SHALL be `(workspace_id, name)` instead of `(name)` alone, allowing different workspaces to have skills with the same name.

#### Scenario: Valid skill name
- **WHEN** a skill is created with name "developer"
- **THEN** it SHALL be accepted

#### Scenario: Unique name per workspace
- **WHEN** a skill is created
- **THEN** the combination of (workspace_id, name) SHALL be unique among non-deleted skills

#### Scenario: System skill with zero UUID
- **WHEN** a skill is created with `source="system"`
- **THEN** `workspace_id` SHALL default to `00000000-0000-0000-0000-000000000000`

#### Scenario: User skill with workspace ID
- **WHEN** a skill is created with `source="user"` by a user in workspace A
- **THEN** `workspace_id` SHALL be set to workspace A's UUID

#### Scenario: Same skill name in different workspaces
- **WHEN** workspace A has a skill named "helper" and workspace B creates a skill named "helper"
- **THEN** both skills SHALL coexist without unique constraint violation

#### Scenario: SkillCreateSchema includes workspace_id
- **WHEN** a skill is created via API
- **THEN** `workspace_id` SHALL be automatically set from the authenticated user's workspace context, not from the request body

### Requirement: IMIdentityBindingModel binds IM users to Hecate users per workspace

The system SHALL provide an `IMIdentityBindingModel` ORM table in `src/hecate/models/im_identity_binding.py` that maps an IM-platform user identity to a Hecate user within a workspace, enabling mandatory Bound Identity enforcement. The table SHALL have columns: `id` (UUID4 primary key), `workspace_id` (UUID, FK to `WorkspaceModel.id`, required, server default zero UUID), `user_id` (UUID, FK to `UserModel.id`, required), `channel_type` (String, required, lowercase identifier like `"feishu"` or `"slack"`), `im_app_id` (String, required, identifies which IM App the binding belongs to), `im_user_id` (String, required, the IM-platform user identifier such as Feishu `open_id` or Slack `U...` ID), `metadata_` (JSON, default `{}`), `created_at`, `updated_at`, `deleted`, `deleted_at`. The model SHALL inherit from the existing `BaseModel`.

#### Scenario: Unique binding per (workspace, channel, app, im_user)

- **WHEN** a binding row is inserted for `(workspace_id=X, channel_type="feishu", im_app_id="cli_xxx", im_user_id="ou_abc")`
- **THEN** a second insertion with the same `(workspace_id, channel_type, im_app_id, im_user_id)` SHALL be rejected by a unique constraint on the active rows (`deleted=False`)

#### Scenario: One Hecate user can bind multiple IM identities

- **WHEN** user `user-X` has already bound Feishu `open_id="ou_abc"` in workspace `ws-A`
- **AND** a new binding is created for the same `user-X` with `channel_type="slack"`, `im_user_id="U123"`
- **THEN** the insertion SHALL succeed — one Hecate user may hold multiple IM bindings

#### Scenario: One IM identity can only bind to one user within a workspace

- **WHEN** user `user-X` has already bound Feishu `open_id="ou_abc"` in workspace `ws-A`
- **AND** a binding attempt is made to bind `ou_abc` to a different `user-Y` in the same `ws-A`
- **THEN** the unique constraint SHALL reject the insertion

#### Scenario: Same IM identity can bind to different users across workspaces

- **WHEN** `open_id="ou_abc"` is bound to `user-X` in workspace `ws-A`
- **AND** a binding attempt is made to bind `ou_abc` to `user-Y` in workspace `ws-B`
- **THEN** the insertion SHALL succeed — workspace_id is part of the unique key

#### Scenario: Workspace isolation on lookup

- **WHEN** code queries `IMIdentityBindingModel` for an inbound message
- **THEN** the query SHALL include `WHERE workspace_id = :workspace_id AND channel_type = :channel_type AND im_app_id = :im_app_id AND im_user_id = :im_user_id AND deleted = false` to prevent cross-tenant leakage

#### Scenario: Soft delete supports unbind

- **WHEN** a user unbinds their IM identity via the Web UI
- **THEN** the binding row SHALL be soft-deleted (set `deleted=true` and `deleted_at=now()`) rather than physically removed, preserving audit history

### Requirement: ConversationModel carries source_channel and im_chat_id

The `ConversationModel` SHALL gain two new nullable columns: `source_channel` (String with length limit 32, nullable) tracking which IM platform or API path originated the conversation, and `im_chat_id` (String with length limit 128, nullable) storing the IM-platform chat identifier for reply routing. Both fields SHALL be added without breaking existing reads — pre-existing rows SHALL have `NULL` for both columns.

#### Scenario: IM conversation has source_channel populated

- **WHEN** a Conversation is created via the IM Gateway path
- **THEN** `ConversationModel.source_channel` SHALL be set to `"feishu"` or `"slack"` (matching the inbound channel)

#### Scenario: API conversation has source_channel NULL

- **WHEN** a Conversation is created via `POST /v1/chat/completions`
- **THEN** `ConversationModel.source_channel` SHALL be `NULL`

#### Scenario: IM chat_id persisted for reply routing

- **WHEN** an Feishu message creates a Conversation
- **THEN** `ConversationModel.im_chat_id` SHALL be set to the Feishu `chat_id` so the response path can target the same chat

#### Scenario: API conversation has im_chat_id NULL

- **WHEN** a Conversation is created via the API path
- **THEN** `ConversationModel.im_chat_id` SHALL be `NULL`

#### Scenario: Existing rows readable after migration

- **WHEN** Alembic migration adds the two columns to an existing `conversations` table
- **THEN** all pre-existing rows SHALL have `source_channel=NULL` and `im_chat_id=NULL`, and existing read queries SHALL return identical data with the new fields being `None`

### Requirement: MessageModel carries source_channel

The `MessageModel` SHALL gain a new nullable column `source_channel` (String with length limit 32, nullable) tracking which IM platform or API path the message originated from. The field SHALL be added without breaking existing reads.

#### Scenario: IM message has source_channel populated

- **WHEN** a `MessageModel` row is created via the IM Gateway path
- **THEN** `MessageModel.source_channel` SHALL be set to the inbound channel name

#### Scenario: API message has source_channel NULL

- **WHEN** a `MessageModel` row is created via the API path
- **THEN** `MessageModel.source_channel` SHALL be `NULL`

#### Scenario: Existing rows readable after migration

- **WHEN** Alembic migration adds the column to an existing `messages` table
- **THEN** all pre-existing rows SHALL have `source_channel=NULL`, and existing read queries SHALL continue to work identically

### Requirement: SessionModel carries source_channel

The `SessionModel` SHALL gain a new nullable column `source_channel` (String with length limit 32, nullable) tracking which IM platform or API path the session was initiated from. The field SHALL be added without breaking existing reads.

#### Scenario: IM session has source_channel populated

- **WHEN** a `SessionModel` row is created via the IM Gateway path
- **THEN** `SessionModel.source_channel` SHALL be set to the inbound channel name

#### Scenario: API session has source_channel NULL

- **WHEN** a `SessionModel` row is created via the API path
- **THEN** `SessionModel.source_channel` SHALL be `NULL`

#### Scenario: Existing rows readable after migration

- **WHEN** Alembic migration adds the column to an existing `sessions` table
- **THEN** all pre-existing rows SHALL have `source_channel=NULL`, and existing read queries SHALL continue to work identically

### Requirement: IMBindingTokenModel provides one-time short-lived binding tokens

The system SHALL provide an `IMBindingTokenModel` ORM table that records pending IM identity binding requests. Columns SHALL include: `id` (UUID4 primary key), `workspace_id` (UUID, FK to `WorkspaceModel.id`, required), `channel_type` (String, required), `im_app_id` (String, required), `im_user_id` (String, required), `bound_user_id` (UUID, nullable, set on confirmation), `token_hash` (String, unique, SHA-256 of the original token), `expires_at` (timestamp, required, `now() + 10 minutes` at creation), `confirmed_at` (timestamp, nullable), `created_at`, `updated_at`, `deleted`, `deleted_at`. The model SHALL inherit from the existing `BaseModel`.

#### Scenario: Token issued on first IM message

- **WHEN** an IM message arrives from an unbound user
- **THEN** the system SHALL create an `IMBindingTokenModel` row with `expires_at = now() + 10 minutes` and SHALL send the IM user the confirmation URL containing the unhashed token

#### Scenario: Token rejected after expiration

- **WHEN** the confirmation endpoint receives a token whose `expires_at` is in the past
- **THEN** the endpoint SHALL return `410 Gone` and SHALL NOT create an `IMIdentityBindingModel`

#### Scenario: Token rejected on second use

- **WHEN** the confirmation endpoint receives a token that has already been used (`confirmed_at IS NOT NULL`)
- **THEN** the endpoint SHALL return `410 Gone` and SHALL NOT create a duplicate `IMIdentityBindingModel`

#### Scenario: Token confirmation creates IMIdentityBindingModel

- **WHEN** the confirmation endpoint receives a valid token and the authenticated Hecate user confirms the binding
- **THEN** the endpoint SHALL set the token's `confirmed_at = now()`, SHALL set `bound_user_id` to the authenticated user, and SHALL create the corresponding `IMIdentityBindingModel` row in a single transaction

#### Scenario: Plaintext token never stored

- **WHEN** the binding token is issued
- **THEN** only the SHA-256 hash of the token SHALL be stored in the database; the plaintext token SHALL only exist in the URL sent to the IM user and SHALL never be persisted

