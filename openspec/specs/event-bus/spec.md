# event-bus Specification

## Purpose
TBD - created by archiving change multi-agent-advanced-collaboration. Update Purpose after archive.
## Requirements
### Requirement: EventBus ABC defines pub/sub interface
The engine SHALL define an `EventBus` ABC in `engine/eventbus.py` with abstract methods: `publish`, `subscribe`, `unsubscribe`, and `close`.

#### Scenario: Publish event
- **WHEN** `publish(topic="agent_researcher", event=CollaborationEvent(...))` is called
- **THEN** the event SHALL be delivered to all subscribers of topic `"agent_researcher"` asynchronously

#### Scenario: Subscribe to topic
- **WHEN** `subscribe(topic="agent_researcher", handler=my_handler)` is called with an async callable
- **THEN** `my_handler` SHALL be invoked for every event published to `"agent_researcher"` until unsubscribed

#### Scenario: Unsubscribe from topic
- **WHEN** `unsubscribe(topic="agent_researcher", handler=my_handler)` is called
- **THEN** `my_handler` SHALL NOT be invoked for subsequent events on that topic

#### Scenario: Close bus
- **WHEN** `close()` is called
- **THEN** all pending events SHALL be flushed to subscribers and internal resources SHALL be released

### Requirement: CollaborationEvent dataclass for agent coordination
The engine SHALL define a `CollaborationEvent` frozen dataclass with fields: `id` (UUID, auto-generated), `topic` (str), `sender` (str, node ID), `event_type` (CollaborationEventType enum), `payload` (dict), `timestamp` (datetime, auto-generated).

#### Scenario: Create collaboration event
- **WHEN** `CollaborationEvent(topic="negotiation", sender="agent_a", event_type=CollaborationEventType.AGENT_MESSAGE, payload={"content": "I propose..."})` is created
- **THEN** it SHALL have auto-generated `id` (UUID) and `timestamp` (UTC now)

#### Scenario: Event immutability
- **WHEN** a CollaborationEvent instance exists
- **THEN** attempting to set any field SHALL raise `FrozenInstanceError`

### Requirement: CollaborationEventType enum for agent-specific events
The engine SHALL define a string enum `CollaborationEventType` with values: `AGENT_MESSAGE`, `AGENT_REQUEST`, `AGENT_RESPONSE`, `TASK_ASSIGNED`, `TASK_COMPLETED`, `NEGOTIATION_PROPOSAL`, `NEGOTIATION_ACCEPT`, `NEGOTIATION_REJECT`, `DEBATE_ARGUMENT`, `DEBATE_REBUTTAL`, `DEBATE_CONCLUSION`.

#### Scenario: Use standard collaboration event type
- **WHEN** `CollaborationEventType.AGENT_MESSAGE` is referenced
- **THEN** it SHALL equal the string `"AGENT_MESSAGE"`

### Requirement: InMemoryEventBus provides session-scoped pub/sub
An `InMemoryEventBus` SHALL implement EventBus using `asyncio.Queue` per topic, suitable for session-scoped agent coordination.

#### Scenario: Publish and receive
- **WHEN** a handler subscribes to topic `"agent_a"`, then `publish("agent_a", event)` is called
- **THEN** the handler SHALL receive the event

#### Scenario: Multiple subscribers
- **WHEN** 3 handlers subscribe to the same topic and an event is published
- **THEN** all 3 handlers SHALL receive the event

#### Scenario: Topic isolation
- **WHEN** a handler subscribes to `"agent_a"` and an event is published to `"agent_b"`
- **THEN** the handler SHALL NOT receive the event

#### Scenario: Unsubscribed handler ignored
- **WHEN** a handler is unsubscribed from a topic and an event is published to that topic
- **THEN** the handler SHALL NOT receive the event

#### Scenario: Close flushes pending events
- **WHEN** 5 events are published to a topic with a subscriber, then `close()` is called
- **THEN** all 5 events SHALL be delivered to the subscriber before close returns

### Requirement: PregelRuntime accepts optional EventBus
PregelRuntime SHALL accept an optional `event_bus: EventBus | None = None` parameter. When provided, the runtime SHALL pass it to workers via `execution_context`.

#### Scenario: EventBus in execution context
- **WHEN** PregelRuntime is created with `event_bus=InMemoryEventBus()`
- **THEN** `execution_context` passed to workers SHALL contain `{"event_bus": <the EventBus instance>}`

#### Scenario: Default no EventBus
- **WHEN** PregelRuntime is created without event_bus
- **THEN** execution_context SHALL NOT contain `"event_bus"` key (or value SHALL be None)

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

