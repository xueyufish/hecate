## ADDED Requirements

### Requirement: Unified execution entry point for all agent modes
The `WorkflowExecutionService` SHALL accept an AgentModel, resolve the appropriate graph template based on `agent.mode`, compile the graph, instantiate production Workers, and execute via PregelRuntime. All three modes (chat, three_layer, workflow) SHALL route through this service.

#### Scenario: Chat mode execution
- **WHEN** `execute(agent_mode="chat", messages=[...], model="gpt-4o", ...)` is called
- **THEN** the service SHALL call `build_chat_graph()` to produce a GraphConfig, compile it, create production Workers, and run PregelRuntime.execute()

#### Scenario: Three_layer mode execution
- **WHEN** `execute(agent_mode="three_layer", messages=[...], ...)` is called
- **THEN** the service SHALL call `build_three_layer_graph()` to produce a GraphConfig, compile it, create production Workers, and run PregelRuntime.execute()

#### Scenario: Workflow mode execution
- **WHEN** `execute(agent_mode="workflow", workflow_id=<uuid>, messages=[...], ...)` is called
- **THEN** the service SHALL load the workflow's current version from database, call `parse_graph(version.graph_dsl)` to produce a GraphConfig, compile it, create production Workers, and run PregelRuntime.execute()

### Requirement: Streaming support through execution service
The `WorkflowExecutionService` SHALL support both streaming and non-streaming execution modes, mapping to PregelRuntime's StreamMode.

#### Scenario: Streaming execution
- **WHEN** `execute(stream=True)` is called
- **THEN** the service SHALL return an AsyncGenerator yielding events from PregelRuntime with StreamMode.MESSAGES

#### Scenario: Non-streaming execution
- **WHEN** `execute(stream=False)` is called
- **THEN** the service SHALL consume PregelRuntime's generator and return the final channel state as a response dict

### Requirement: Session and evidence metadata propagation
The `WorkflowExecutionService` SHALL propagate session_id, agent_id, user_id, and turn_index through channel state so Workers can access them for evidence tracking, memory operations, and suggestion generation.

#### Scenario: Metadata in channels
- **WHEN** execute is called with session_id, agent_id, user_id
- **THEN** the service SHALL inject `{"_session_id": ..., "_agent_id": ..., "_user_id": ..., "_turn_index": 0}` into initial_input channels before PregelRuntime execution
