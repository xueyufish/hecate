## ADDED Requirements

### Requirement: AgentState data model
The system SHALL provide an `AgentState` Pydantic model representing per-session working state. Each AgentState instance is scoped to a single (agent_id, session_id) pair.

#### Scenario: AgentState has required fields
- **WHEN** an AgentState is created
- **THEN** it contains `session_id`, `agent_id`, `summary`, `context`, `permission_context`, `tool_context`, `task_context`, `environment_root`, and `metadata` fields

#### Scenario: AgentState defaults to empty state
- **WHEN** an AgentState is created with only session_id and agent_id
- **THEN** `summary` defaults to empty string, `context` defaults to empty list, and all sub-contexts default to empty dicts

#### Scenario: AgentState is serializable to JSON
- **WHEN** `model_dump()` is called on an AgentState instance
- **THEN** a JSON-serializable dict is returned containing all fields

#### Scenario: AgentState is deserializable from JSON
- **WHEN** `AgentState.model_validate(data)` is called with a valid dict
- **THEN** an AgentState instance is returned with matching field values

### Requirement: AgentStateStore abstract interface
The system SHALL provide an `AgentStateStore` abstract base class defining the persistence contract for AgentState.

#### Scenario: Save state
- **WHEN** `save(agent_id, session_id, state)` is called
- **THEN** the AgentState is persisted and can be retrieved by the same (agent_id, session_id) key

#### Scenario: Load existing state
- **WHEN** `load(agent_id, session_id)` is called and state exists for that key
- **THEN** the previously saved AgentState is returned

#### Scenario: Load non-existent state
- **WHEN** `load(agent_id, session_id)` is called and no state exists for that key
- **THEN** `None` is returned

#### Scenario: Delete state
- **WHEN** `delete(agent_id, session_id)` is called
- **THEN** subsequent `load()` for the same key returns `None`

#### Scenario: List sessions for agent
- **WHEN** `list_sessions(agent_id)` is called
- **THEN** a list of session summaries (session_id, updated_at) is returned for all sessions belonging to that agent

### Requirement: InMemoryStateStore implementation
The system SHALL provide an `InMemoryStateStore` implementing `AgentStateStore` for single-process use and testing.

#### Scenario: In-memory persistence within process
- **WHEN** state is saved to InMemoryStateStore
- **THEN** it can be loaded within the same process lifetime

#### Scenario: State lost on process restart
- **WHEN** the process exits
- **THEN** all state in InMemoryStateStore is lost (expected behavior for MVP)

#### Scenario: Concurrent access safety
- **WHEN** two coroutines save state for the same (agent_id, session_id) concurrently
- **THEN** no data corruption occurs (asyncio.Lock serializes writes)

#### Scenario: Different sessions are independent
- **WHEN** state is saved for session A and session B of the same agent
- **THEN** loading session A returns session A's state, not session B's

### Requirement: WorkflowExecutionService state lifecycle
The system SHALL integrate AgentState load/save lifecycle into `WorkflowExecutionService.execute()`.

#### Scenario: State loaded at call entry
- **WHEN** `execute()` is called with a session_id that has existing state
- **THEN** the AgentState is loaded from the store and injected into `execution_context["_agent_state"]`

#### Scenario: Fresh state created when none exists
- **WHEN** `execute()` is called with a session_id that has no existing state
- **THEN** a new empty AgentState is created with the given session_id and agent_id

#### Scenario: State saved at call exit
- **WHEN** `execute()` completes (both streaming and non-streaming)
- **THEN** the current AgentState is saved to the store

#### Scenario: State persists across calls
- **WHEN** `execute()` is called twice with the same session_id
- **THEN** the second call sees the state from the first call (context, summary, etc.)

#### Scenario: Environment root populated automatically
- **WHEN** `execute()` is called with an agent_id and EnvironmentManager is configured
- **THEN** the AgentState's `environment_root` field is populated from the agent's environment

### Requirement: AgentStateStore is optional
The system SHALL function without an AgentStateStore configured (backward compatibility).

#### Scenario: No state store configured
- **WHEN** WorkflowExecutionService is created without an AgentStateStore
- **THEN** execute() behaves exactly as before (no state persistence, no errors)
