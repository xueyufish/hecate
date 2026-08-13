## ADDED Requirements

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