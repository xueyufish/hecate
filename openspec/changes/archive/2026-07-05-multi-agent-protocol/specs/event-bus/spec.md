## ADDED Requirements

### Requirement: CollaborationEventType supports A2A-specific events
The engine SHALL extend the `CollaborationEventType` enum with A2A-specific values: `A2A_TASK_DELEGATED`, `A2A_TASK_RECEIVED`, `A2A_ARTIFACT_SENT`, `A2A_ARTIFACT_RECEIVED`, `A2A_AGENT_DISCOVERED`.

#### Scenario: Publish A2A task delegated event
- **WHEN** an A2A client delegates a task to a remote agent
- **THEN** the EventBus SHALL support publishing an event with `event_type: CollaborationEventType.A2A_TASK_DELEGATED` containing the remote agent URL and task ID in the payload

#### Scenario: Subscribe to A2A artifact events
- **WHEN** a handler subscribes to topic `"a2a_artifacts"`
- **THEN** the handler SHALL receive events with `event_type: CollaborationEventType.A2A_ARTIFACT_RECEIVED` when artifacts arrive from remote A2A agents

### Requirement: EventBus supports A2A task correlation
The EventBus SHALL allow correlating A2A task IDs with local collaboration topics, enabling event handlers to filter events by A2A task context.

#### Scenario: Correlate A2A task with local topic
- **WHEN** an A2A task with ID `task-123` is received and local execution publishes events to topic `"agent_worker"`
- **THEN** the events SHALL include `a2a_task_id: "task-123"` in the payload metadata, allowing correlation queries
