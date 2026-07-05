## ADDED Requirements

### Requirement: AgentTool supports remote A2A agent targets
The engine SHALL extend the `AgentTool` class to support remote A2A agent targets in addition to local agent IDs, enabling agents to invoke remote A2A agents as tools via the A2AClient.

#### Scenario: AgentTool with remote A2A target
- **WHEN** an AgentTool is created with a remote agent URL instead of a local agent UUID
- **THEN** the tool's `execute()` method SHALL delegate to `A2AClient.send_message(remote_url, message)` instead of `EnginePort.agent_execute()`

#### Scenario: Remote agent timeout
- **WHEN** a remote A2A agent invocation exceeds the configured timeout
- **THEN** the AgentTool SHALL return an error dict with `{"error": "Remote agent timed out", "timed_out": True}`

#### Scenario: Remote agent error propagation
- **WHEN** a remote A2A agent returns a task with `state: TASK_STATE_FAILED`
- **THEN** the AgentTool SHALL extract the error message from the task status and return it as a tool execution error

### Requirement: AgentTool supports workflow targets
The engine SHALL enable AgentTool to wrap workflows as callable tools via the `WorkflowTool` class, providing a parallel interface to AgentTool but delegating to `EnginePort.workflow_execute()`.

#### Scenario: WorkflowTool as agent-callable tool
- **WHEN** a WorkflowTool is registered in an agent's tool list
- **THEN** the LLM SHALL see the workflow as a callable tool with a name, description, and parameter schema derived from the workflow's Start Node variables
