## ADDED Requirements

### Requirement: Publish-subscribe message bus
The system SHALL provide an event-driven message bus for agent communication.

#### Scenario: Publish message
- **WHEN** an agent publishes a message to a topic
- **THEN** all subscribers of that topic receive the message

#### Scenario: Subscribe to topic
- **WHEN** an agent subscribes to a topic
- **THEN** it receives all future messages published to that topic

### Requirement: Message routing
The system SHALL route messages based on topic and agent configuration.

#### Scenario: Direct message
- **WHEN** an agent sends a direct message to another agent
- **THEN** only the target agent receives the message

#### Scenario: Broadcast message
- **WHEN** an agent broadcasts a message
- **THEN** all agents in the workspace receive the message
