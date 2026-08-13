# im-channel-feishu-slack Specification

## Purpose
TBD - created by archiving change multi-channel-feishu-slack. Update Purpose after archive.
## Requirements
### Requirement: Feishu IM channel adapter implements ChannelABC

The system SHALL provide a `FeishuChannel` class in `src/hecate/channel/im/feishu.py` that subclasses the existing `hecate.channel.adapter.ChannelABC` and wraps the official `lark_oapi.channel.FeishuChannel` SDK as a thin normalization layer. The adapter SHALL translate `lark_oapi.channel.InboundMessage` to `CanonicalMessage` (with `channel_id="feishu"`) on `receive()` and translate `CanonicalMessage` / response objects to `lark_oapi.channel.OutboundMessage` on `respond()` and `stream()`.

#### Scenario: Receive a text message from Feishu

- **WHEN** a Feishu text message event arrives via the webhook endpoint
- **THEN** `FeishuChannel.receive(raw_event)` SHALL return a `CanonicalMessage` with `channel_id="feishu"`, `user_id` set to the IM `open_id` from the event, `content.text` set to the message text, and `metadata["chat_id"]` set to the source `chat_id`

#### Scenario: Respond to a Feishu message

- **WHEN** `FeishuChannel.respond(message_id, response)` is called with a response object containing target `chat_id`
- **THEN** the adapter SHALL call `lark_oapi.channel.FeishuChannel.send(chat_id, ...)` to post the message and SHALL return when the IM platform acknowledges

#### Scenario: Stream response to Feishu (basic)

- **WHEN** `FeishuChannel.stream(message_id, chunks)` is called with a single text chunk iterator in MVP
- **THEN** the adapter SHALL collect all chunks into a single message and post it via `respond()` semantics; full streaming card updates are deferred to Phase 2

#### Scenario: Declare capabilities

- **WHEN** `FeishuChannel.capabilities` is queried
- **THEN** it SHALL return `ChannelCapabilities(streaming=True, markdown=True, rich_cards=True, file_upload=True, max_message_length=30000)`

### Requirement: Slack IM channel adapter implements ChannelABC

The system SHALL provide a `SlackChannel` class in `src/hecate/channel/im/slack.py` that subclasses the existing `hecate.channel.adapter.ChannelABC` and wraps the official `slack_bolt.App` SDK as a thin normalization layer. The adapter SHALL translate Slack `event` payloads to `CanonicalMessage` (with `channel_id="slack"`) on `receive()` and translate `CanonicalMessage` / response objects to Slack Block Kit messages on `respond()` and `stream()`.

#### Scenario: Receive a text message from Slack

- **WHEN** a Slack `message` event (or `app_mention`) arrives via the webhook endpoint
- **THEN** `SlackChannel.receive(raw_event)` SHALL return a `CanonicalMessage` with `channel_id="slack"`, `user_id` set to the Slack user `U...` ID, `content.text` set to the message text, and `metadata["channel_id"]` set to the source Slack channel ID

#### Scenario: Respond to a Slack message

- **WHEN** `SlackChannel.respond(message_id, response)` is called with a response object containing target channel ID
- **THEN** the adapter SHALL call Slack Web API `chat.postMessage` with Block Kit blocks (falling back to plain text if blocks are unsupported) and SHALL return when Slack acknowledges

#### Scenario: Declare capabilities

- **WHEN** `SlackChannel.capabilities` is queried
- **THEN** it SHALL return `ChannelCapabilities(streaming=True, markdown=False, rich_cards=True, interactive_buttons=True, file_upload=True, max_message_length=40000)` — Slack uses `mrkdwn` not standard markdown

### Requirement: Webhook endpoint dispatches to registered IM channel adapters

The system SHALL provide a FastAPI route `POST /v1/channels/{name}/webhook` that accepts inbound webhooks from IM platforms. The route SHALL resolve `{name}` against the `PluginRegistry` (type=`channel`), validate the inbound request with the adapter's transport handler (signature verification, challenge response), call `ChannelABC.receive(raw_body)` to obtain a `CanonicalMessage`, enqueue it to the MessageBus, and return `200 OK` within 200 milliseconds.

#### Scenario: Slack URL verification challenge

- **WHEN** Slack sends a `url_verification` event with `{"token": "...", "challenge": "abc123", "type": "url_verification"}` to the Slack webhook endpoint
- **THEN** the endpoint SHALL return `200 OK` with body `{"challenge": "abc123"}` without enqueuing any message

#### Scenario: Signed Slack event accepted

- **WHEN** Slack sends a signed `event_callback` payload with valid `X-Slack-Signature` and `X-Slack-Request-Timestamp` to the Slack webhook endpoint
- **THEN** the endpoint SHALL verify the signature, normalize the event to `CanonicalMessage`, enqueue it to the MessageBus, and return `200 OK` within 200 milliseconds

#### Scenario: Invalid Slack signature rejected

- **WHEN** Slack sends a payload with an invalid `X-Slack-Signature`
- **THEN** the endpoint SHALL return `401 Unauthorized` without enqueuing any message

#### Scenario: Feishu challenge accepted

- **WHEN** Feishu sends a `url_verification` challenge to the Feishu webhook endpoint
- **THEN** the endpoint SHALL respond according to the Feishu challenge protocol and return `200 OK`

#### Scenario: Unknown channel name rejected

- **WHEN** a webhook arrives at `/v1/channels/nonexistent/webhook`
- **THEN** the endpoint SHALL return `404 Not Found`

### Requirement: IM identity binding is mandatory before conversation

The system SHALL enforce that every inbound IM message resolves to a known `(workspace_id, user_id)` via the `im_identity_bindings` table before being routed to an Agent. IM users without a binding record SHALL be rejected and the webhook adapter SHALL send a one-time binding token link in response. The binding SHALL be created only via the Web UI flow, never automatically.

#### Scenario: Unbound IM user is rejected

- **WHEN** an inbound Feishu message arrives with `open_id="ou_abc"` and no corresponding row exists in `im_identity_bindings`
- **THEN** the system SHALL NOT route the message to an Agent and SHALL send a binding prompt message to the IM user containing a binding URL of the form `https://{host}/v1/im/bindings/confirm?token={one_time_token}`

#### Scenario: Bound IM user is routed

- **WHEN** an inbound Feishu message arrives with `open_id="ou_abc"` and a row exists in `im_identity_bindings` with `(channel_type="feishu", im_user_id="ou_abc")` mapping to `user_id="user-X"` and `workspace_id="ws-A"`
- **THEN** the system SHALL resolve `effective_user = "user-X"` and route the message to `user-X`'s conversation thread within `workspace_id="ws-A"`

#### Scenario: Token expires after 10 minutes

- **WHEN** a binding token was issued 11 minutes ago and an IM user clicks the binding URL
- **THEN** the confirmation endpoint SHALL return `410 Gone` and refuse to create the binding

#### Scenario: Token is single-use

- **WHEN** a binding token has been used successfully once and the user clicks the same URL again
- **THEN** the confirmation endpoint SHALL return `410 Gone` and refuse to create a duplicate binding

### Requirement: MessageBus decouples webhook ACK from Agent execution

The system SHALL provide an in-process MessageBus that accepts `CanonicalMessage` from the webhook endpoint and dispatches Agent execution as a background asyncio task. The webhook SHALL return `200 OK` to the IM platform before Agent execution completes; Agent results SHALL be routed back to the source channel via `ChannelABC.stream()` (basic text) or `respond()` (complete message).

#### Scenario: Webhook returns within 200ms

- **WHEN** a signed IM webhook arrives and is enqueued to the MessageBus
- **THEN** the webhook endpoint SHALL return `200 OK` to the IM platform within 200 milliseconds even if Agent execution takes minutes

#### Scenario: Background task processes message

- **WHEN** the MessageBus enqueues a `CanonicalMessage` from Feishu
- **THEN** the MessageBus SHALL spawn an asyncio task that invokes `WorkflowExecutionService.execute(...)` and routes the result back via `FeishuChannel.respond()` or `stream()`

#### Scenario: MessageBus errors are logged not raised

- **WHEN** the background asyncio task raises an exception during Agent execution
- **THEN** the exception SHALL be logged with the original `message_id` and `channel_id` and SHALL NOT propagate to the webhook handler (which has already returned 200 OK)

### Requirement: Cross-channel session history sharing via deterministic hashing

The system SHALL derive a deterministic `conversation_id` from the resolved `effective_user_id` and the IM context (channel type + app ID), so that the same Hecate user bound to multiple IM channels shares one conversation thread across all channels.

#### Scenario: Same user on Feishu and Slack shares conversation

- **WHEN** user `user-X` has bound `open_id="ou_abc"` on Feishu and `U123` on Slack in the same workspace
- **AND** user sends a message on Feishu creating conversation `conv-A`
- **AND** the same user later sends a message on Slack
- **THEN** the Slack message SHALL be appended to `conv-A` (same conversation) and SHALL NOT create `conv-B`

#### Scenario: Different users get separate conversations

- **WHEN** user `user-X` is bound to Feishu `open_id="ou_abc"` and user `user-Y` is bound to Feishu `open_id="ou_def"`
- **THEN** messages from `ou_abc` SHALL route to `user-X`'s conversation and messages from `ou_def` SHALL route to `user-Y`'s conversation, never cross-contaminated

#### Scenario: Cross-workspace isolation

- **WHEN** user `user-X` in `workspace-A` is bound to Feishu `open_id="ou_abc"` and another `user-X` in `workspace-B` happens to have the same user ID
- **THEN** the routing SHALL scope the binding lookup by `workspace_id` and SHALL NOT leak messages across workspaces

### Requirement: IM App credentials stored via SecretProviderABC

The system SHALL store IM App credentials (Feishu `app_id`/`app_secret`, Slack `bot_token`/`signing_secret`/`app_token`) via the existing `hecate.vault.provider.SecretProviderABC`, indexed by a stable path convention per workspace and channel type. Credentials SHALL NOT be stored in plain text in any database table or environment variable exposed to application logs.

#### Scenario: Credential retrieved at webhook time

- **WHEN** a webhook arrives at `/v1/channels/feishu/webhook` for workspace `ws-A`
- **THEN** the system SHALL call `secret_provider.get_secret("hecate/im/{ws-A}/feishu/app_secret")` (or equivalent path) to retrieve the Feishu `app_secret` for signature verification

#### Scenario: Missing credential returns clear error

- **WHEN** a webhook arrives for a workspace that has no configured IM App credentials
- **THEN** the webhook SHALL return `503 Service Unavailable` with an error message indicating missing credentials for that workspace, and SHALL log the event for ops investigation

#### Scenario: Credential not logged

- **WHEN** any code path logs a credential-related operation
- **THEN** the secret value (app_secret, signing_secret, bot_token) SHALL be redacted to `***` and never appear in full in any log output

### Requirement: IM conversation and message records carry channel provenance

The system SHALL persist the source channel type on every Conversation, Session, and Message created via the IM path, enabling audit, filtering, and routing back to the correct IM target on response. The `source_channel` field SHALL be nullable, with `NULL` indicating messages created via the OpenAI-compatible API path.

#### Scenario: Feishu message persists source_channel

- **WHEN** a Feishu message is processed by the Gateway
- **THEN** the created `ConversationModel.source_channel` SHALL be `"feishu"`, the `MessageModel.source_channel` SHALL be `"feishu"`, and the `SessionModel.source_channel` SHALL be `"feishu"`

#### Scenario: Slack message persists source_channel

- **WHEN** a Slack message is processed by the Gateway
- **THEN** `source_channel` SHALL be `"slack"` on the created Conversation, Message, and Session records

#### Scenario: OpenAI API path leaves source_channel NULL

- **WHEN** a request arrives at `POST /v1/chat/completions`
- **THEN** the created Conversation/Message/Session records SHALL have `source_channel=NULL` and SHALL behave identically to pre-change behavior

#### Scenario: Conversation persists im_chat_id for reply routing

- **WHEN** an Feishu message creates a Conversation
- **THEN** `ConversationModel.im_chat_id` SHALL be populated with the Feishu `chat_id` to enable response routing back to the same chat

### Requirement: Adapter registration via existing PluginRegistry

The system SHALL register `FeishuChannel` and `SlackChannel` via the existing `hecate.gateway.registration.register_channels()` function using the `PluginManifest(type="channel", ...)` pattern. The registration SHALL be opt-in based on environment configuration (presence of credentials) so that deployments without IM credentials do not register the adapters.

#### Scenario: Adapter registered when credentials present

- **WHEN** the application starts with `HECATE_IM_FEISHU_APP_ID` and `HECATE_IM_FEISHU_APP_SECRET` environment variables set
- **THEN** `register_channels()` SHALL register `FeishuChannel` with `name="feishu"` in the `PluginRegistry`

#### Scenario: Adapter not registered when credentials missing

- **WHEN** the application starts without IM environment variables
- **THEN** `register_channels()` SHALL NOT register `FeishuChannel` or `SlackChannel`, and webhook requests for those channels SHALL return `503 Service Unavailable`

#### Scenario: Adapter discoverable by name

- **WHEN** code looks up `plugin_registry.get("channel", "feishu")`
- **THEN** it SHALL return the registered `FeishuChannel` instance if registered, otherwise `None`

