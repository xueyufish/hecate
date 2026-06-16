## ADDED Requirements

### Requirement: Per-Session Sequential Processing
The system SHALL ensure that only one message is processed at a time within a single conversation/session. When a new message arrives for a session that is already processing, the system SHALL queue the message and process it after the current message completes.

#### Scenario: Single message processing
- **WHEN** a user sends a message to an idle session
- **THEN** the message SHALL be processed immediately without queuing

#### Scenario: Concurrent messages queued
- **WHEN** a user sends message B while message A is still processing for the same session
- **THEN** message B SHALL be queued and processed after message A completes

#### Scenario: Multiple queued messages
- **WHEN** messages B, C, D are queued while message A is processing
- **THEN** they SHALL be processed in FIFO order: A → B → C → D

### Requirement: Queue Status Feedback
The system SHALL return queue status information in the response headers when a message is queued or processing.

#### Scenario: Message processing immediately
- **WHEN** a message is processed without waiting
- **THEN** the response SHALL include header `X-Queue-Position: 0`

#### Scenario: Message queued
- **WHEN** a message is queued behind N other messages
- **THEN** the response SHALL include header `X-Queue-Position: N` and `X-Queue-Wait-Ms: <milliseconds>`

### Requirement: Queue Timeout
Queued messages SHALL timeout after 5 minutes. If a message exceeds the timeout while waiting in queue, the system SHALL return HTTP 408 Request Timeout.

#### Scenario: Message times out in queue
- **WHEN** a message waits more than 5 minutes in queue
- **THEN** the system SHALL return HTTP 408 with a message indicating the queue timeout

#### Scenario: Message processes within timeout
- **WHEN** a message is dequeued and processed within 5 minutes
- **THEN** the system SHALL process normally without timeout error

### Requirement: Different Sessions Independent
Messages for different sessions SHALL be processed independently without blocking each other. A busy session A SHALL NOT block session B.

#### Scenario: Independent sessions
- **WHEN** session A is processing a long message and session B receives a new message
- **THEN** session B's message SHALL be processed immediately without waiting for session A

### Requirement: Queue Indicator in Chat UI
The frontend chat page SHALL display a queue indicator when a message is queued. The indicator SHALL show the queue position and update as messages are processed.

#### Scenario: Message queued in chat
- **WHEN** the user sends a message and it is queued (X-Queue-Position > 0)
- **THEN** the chat UI SHALL display "Queued (position N)..." indicator

#### Scenario: Message starts processing
- **WHEN** a queued message begins processing
- **THEN** the queue indicator SHALL be removed and the response SHALL stream normally
