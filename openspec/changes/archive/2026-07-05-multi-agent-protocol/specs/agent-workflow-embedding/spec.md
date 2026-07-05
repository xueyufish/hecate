## ADDED Requirements

### Requirement: Agents can invoke workflows as tools
The system SHALL enable agents to invoke registered workflows as callable tools via the `EnginePort.workflow_execute()` method, allowing multi-step logic to be encapsulated and reused across agents.

#### Scenario: Agent invokes a workflow as a tool
- **WHEN** an agent's LLM generates a tool call for a workflow-type skill
- **THEN** the system invokes `EnginePort.workflow_execute(workflow_id, input, context)` which executes the workflow via PregelRuntime and returns the result

#### Scenario: Workflow tool parameters auto-generated
- **WHEN** a workflow is registered as a skill
- **THEN** the system generates a JSON Schema for the workflow's input variables (from the Start Node configuration) and exposes it as the tool's parameter schema

### Requirement: Workflows can embed agents as DAG nodes
The system SHALL support AGENT-type nodes in workflow DAGs that delegate execution to a specified agent via `EnginePort.agent_execute()`, with channel state passthrough.

#### Scenario: Workflow with agent node
- **WHEN** a workflow DAG contains an AGENT node with `agent_id: "<uuid>"`
- **THEN** the AgentWorker SHALL invoke `EnginePort.agent_execute()` with the agent ID, messages from incoming channels, and the channel snapshot as context

#### Scenario: Agent node passes channel state
- **WHEN** an AGENT node executes in a workflow
- **THEN** the agent SHALL receive the current channel snapshot as context, and the agent's response SHALL be written to the node's output channel

### Requirement: Recursive nesting is limited to depth 3
The system SHALL enforce a maximum nesting depth of 3 for Agent → Workflow → Agent chains, raising a clear error when the limit is exceeded.

#### Scenario: Nesting within limit
- **WHEN** Agent A invokes Workflow W1, which contains Agent B, which invokes Workflow W2 (depth 2)
- **THEN** the system executes the chain successfully

#### Scenario: Nesting exceeds limit
- **WHEN** the nesting depth reaches 4 (Agent → WF → Agent → WF → Agent → WF → Agent)
- **THEN** the system raises a `NestingDepthExceededError` with a message indicating the maximum depth of 3 and the current depth

#### Scenario: Depth tracking via context
- **WHEN** an agent or workflow is invoked
- **THEN** the system tracks the current nesting depth in the execution context, incrementing on each Agent → Workflow or Workflow → Agent transition

### Requirement: WorkflowTool wraps workflows as agent-callable tools
The system SHALL provide a `WorkflowTool` class (analogous to `AgentTool`) that wraps a workflow ID and exposes it as a callable tool for LLM invocation.

#### Scenario: WorkflowTool generates tool schema
- **WHEN** a WorkflowTool is created for workflow W1 with input variables `query` and `context`
- **THEN** the tool's JSON Schema SHALL have `name: "workflow_<wf_name>"`, `description` from the workflow, and `parameters` matching the input variables

#### Scenario: WorkflowTool executes workflow
- **WHEN** an LLM calls a WorkflowTool with arguments
- **THEN** the tool delegates to `EnginePort.workflow_execute()` with the workflow ID, arguments as input, and current channel snapshot as context

### Requirement: Workflow execution reuses existing PregelRuntime
The system SHALL execute embedded workflows using the existing PregelRuntime and GraphCompiler infrastructure, ensuring all guardrails, tracing, and event store logging apply to nested workflow execution.

#### Scenario: Embedded workflow triggers tracing
- **WHEN** an agent invokes a workflow via WorkflowTool
- **THEN** the workflow execution SHALL create trace spans under the parent agent's trace, visible in the Full-Chain Tracing system

#### Scenario: Embedded workflow respects guardrails
- **WHEN** an embedded workflow contains an LLM node
- **THEN** the existing PreLLMHook and PostLLMHook guardrails SHALL fire for the embedded LLM call
