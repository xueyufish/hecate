## ADDED Requirements

### Requirement: ChannelABC defines external platform adapter interface
The system SHALL define a `ChannelABC` abstract base class in `channel/adapter.py` with the following abstract interface: `name` property, `description` property, `capabilities` property returning `ChannelCapabilities`, `receive(raw)` method, `respond(message_id, response)` method, and `stream(message_id, chunks)` method.

#### Scenario: Concrete channel implementation
- **WHEN** a class extends ChannelABC and implements all abstract methods
- **THEN** it SHALL be registerable with PluginRegistry under type="channel"

#### Scenario: Missing abstract method
- **WHEN** a class extends ChannelABC but does not implement `receive()`
- **THEN** instantiation SHALL raise TypeError

### Requirement: CanonicalMessage is the universal message format
The system SHALL define a frozen `CanonicalMessage` dataclass with fields: `id` (UUID), `channel_id` (str), `user_id` (str), `session_id` (str | None), `content` (MessageContent), `metadata` (dict), `timestamp` (datetime). `MessageContent` SHALL contain `text` (str | None) and `attachments` (tuple of Attachment objects).

#### Scenario: Create canonical message from text
- **WHEN** a CanonicalMessage is created with `content=MessageContent(text="hello")`
- **THEN** it SHALL be immutable (frozen dataclass)

#### Scenario: Metadata passthrough
- **WHEN** a CanonicalMessage is created with `metadata={"telegram_chat_id": "123"}`
- **THEN** the metadata dict SHALL be preserved as-is for downstream platform-specific logic

### Requirement: ChannelCapabilities declares platform support
The system SHALL define a frozen `ChannelCapabilities` dataclass with boolean fields: `streaming`, `interactive_buttons`, `file_upload`, `markdown`, `rich_cards`, and optional `max_message_length` (int | None). All boolean fields SHALL default to `False`.

#### Scenario: Channel declares streaming support
- **WHEN** a channel's `capabilities` property returns `ChannelCapabilities(streaming=True)`
- **THEN** the Gateway SHALL use streaming for responses to that channel

#### Scenario: Channel without streaming
- **WHEN** a channel's `capabilities` returns `ChannelCapabilities(streaming=False)`
- **THEN** the Gateway SHALL buffer the full response before sending

### Requirement: Gateway routes messages from channels to agent runtime
The system SHALL implement a `Gateway` class that accepts `CanonicalMessage` from channels, resolves session context, and delegates to `WorkflowExecutionService`. The Gateway SHALL be stateless and not modify the message content.

#### Scenario: Gateway receives message from REST channel
- **WHEN** a RESTChannelAdapter sends a CanonicalMessage to Gateway
- **THEN** Gateway SHALL resolve the session (create or resume) and call WorkflowExecutionService

#### Scenario: Gateway receives message from unknown channel
- **WHEN** a CanonicalMessage arrives with `channel_id` not matching any registered channel
- **THEN** Gateway SHALL raise ValueError

### Requirement: Gateway session routing
The Gateway SHALL maintain a mapping of `session_id → (channel_id, user_id)`. When a message arrives with an existing `session_id`, the Gateway SHALL route it to the same session. When `session_id` is None, the Gateway SHALL create a new session.

#### Scenario: Resume existing session
- **WHEN** a CanonicalMessage arrives with `session_id="abc"` that was previously created by channel "feishu"
- **THEN** Gateway SHALL route to the existing session, regardless of which channel the new message came from

#### Scenario: Create new session
- **WHEN** a CanonicalMessage arrives with `session_id=None`
- **THEN** Gateway SHALL create a new session and assign a UUID

### Requirement: NotificationDispatcher becomes outbound Channel
The existing `NotificationDispatcher` SHALL be refactored to use Channel adapters for outbound dispatch. Each notification target (Email, Feishu card, Slack Block Kit, generic webhook) SHALL become a Channel adapter with a `respond()` method that sends alert notifications.

#### Scenario: Send alert via Feishu channel
- **WHEN** an AlertEvent is dispatched to a Feishu channel adapter
- **THEN** the adapter SHALL call `respond()` with the Feishu card payload rendered from the alert

#### Scenario: Send alert via email channel
- **WHEN** an AlertEvent is dispatched to an Email channel adapter
- **THEN** the adapter SHALL call `respond()` with the HTML email rendered from the alert

### Requirement: Existing REST API preserved during migration
The existing `api/v1/chat.py` endpoint SHALL continue to function as-is. A `RESTChannelAdapter` SHALL be created that wraps the same logic, allowing gradual migration to the Gateway pattern without breaking the API contract.

#### Scenario: Existing REST API continues to work
- **WHEN** a client sends POST /v1/chat/completions
- **THEN** the response SHALL be identical to the current behavior (no change)

#### Scenario: RESTChannelAdapter can be used via Gateway
- **WHEN** a RESTChannelAdapter is registered with the Gateway
- **THEN** messages from the REST API SHALL be routable through the Gateway

### Requirement: Channel plugin registration via PluginRegistry
Channel adapters SHALL be registered with PluginRegistry under `type="channel"`. The manifest SHALL include the channel's `name`, `description`, and `capabilities`.

#### Scenario: Register a new channel adapter
- **WHEN** `registry.register(manifest, feishu_adapter)` is called with `type="channel"`
- **THEN** the adapter SHALL be retrievable via `registry.get_by_name("feishu")`

#### Scenario: List all registered channels
- **WHEN** `registry.get_by_type("channel")` is called
- **THEN** it SHALL return all registered channel adapters
