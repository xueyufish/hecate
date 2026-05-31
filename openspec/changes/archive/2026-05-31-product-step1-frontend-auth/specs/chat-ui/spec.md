## ADDED Requirements

### Requirement: Chat interface
The system SHALL provide a chat interface for conversing with an agent in real-time.

#### Scenario: Send message
- **WHEN** user types a message and presses Enter or clicks Send
- **THEN** system sends the message to `POST /v1/chat/completions` and displays the response in real-time via SSE streaming

#### Scenario: Streaming display
- **WHEN** agent is generating a response
- **THEN** system displays text tokens as they arrive, with a typing indicator during generation

#### Scenario: Chat initialization
- **WHEN** user opens chat for an agent
- **THEN** system creates a new conversation (or loads existing) and displays the chat interface with input box

### Requirement: Tool call display
The system SHALL display tool invocations and their results inline in the chat.

#### Scenario: Tool call shown
- **WHEN** agent invokes a tool during generation
- **THEN** system displays a collapsible block showing tool name, arguments, and result

### Requirement: Conversation history
The system SHALL load and display previous messages in the current conversation.

#### Scenario: Load history
- **WHEN** user opens an existing conversation
- **THEN** system loads all previous messages from `GET /api/conversations/{id}` and displays them

### Requirement: New conversation
The system SHALL allow starting a new conversation with an agent.

#### Scenario: Start new chat
- **WHEN** user clicks "New Chat" button
- **THEN** system creates a new conversation and displays empty chat interface
