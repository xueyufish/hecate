## MODIFIED Requirements

### Requirement: EnginePort agent_execute method
The system SHALL add an `agent_execute` method to `EnginePort` that accepts an agent_id, messages, channel_snapshot, and optional context, and returns a dict containing the agent's response. The concrete implementation SHALL load the agent's configured tools, query knowledge bases, apply guard hooks (PreLLMHook/PostLLMHook), and call context_assemble before invoking the LLM — matching the full pipeline used by LLMWorker for CONVERSATION nodes.

#### Scenario: Agent execution via port
- **WHEN** `agent_execute(agent_id=UUID("..."), messages=[{"role": "user", "content": "hello"}], channel_snapshot={})` is called
- **THEN** the port resolves the AgentModel by ID, loads the agent's configured tools, queries the agent's knowledge bases, applies PreLLMHook, calls context_assemble, invokes the LLM with tools, applies PostLLMHook, and returns `{"response": "...", "usage": {...}}`

#### Scenario: Agent not found
- **WHEN** `agent_execute(agent_id=UUID("nonexistent"), ...)` is called
- **THEN** the port raises `ValueError` with message indicating the agent was not found

#### Scenario: Agent execution with tool filtering
- **WHEN** `agent_execute` is called with an `agent_definition` that specifies `tools: ["web_search", "lookup_invoice"]`
- **THEN** only the tools listed in the AgentDefinition are passed to the LLM, not the agent's full tool list

#### Scenario: Agent execution with knowledge bases
- **WHEN** the agent has knowledge bases configured in its AgentModel
- **THEN** the system queries those knowledge bases using the user's messages as context and injects relevant chunks into the LLM context

#### Scenario: PreLLMHook blocks agent execution
- **WHEN** PreLLMHook returns action=BLOCK for the agent's messages
- **THEN** the system returns a response indicating the request was blocked, without invoking the LLM

### Requirement: AGENT node real execution
The system SHALL execute AGENT-type nodes based on the `invocation_mode` field in node configuration. When `invocation_mode` is `"graph"` (default), the worker delegates to WorkflowExecutionService for nested graph execution. When `invocation_mode` is `"tool"`, the target agent is registered as a callable tool via AgentDefinition.

#### Scenario: AGENT node with graph invocation mode
- **WHEN** a graph executes an AGENT node with config `{"agent_id": "uuid-of-agent", "invocation_mode": "graph"}`
- **THEN** the worker delegates to WorkflowExecutionService for nested graph execution (existing behavior)

#### Scenario: AGENT node with tool invocation mode
- **WHEN** a graph executes an AGENT node with config `{"agent_id": "uuid-of-specialist", "invocation_mode": "tool", "agent_definition": {"tools": ["web_search"], "context_mode": "isolated"}}`
- **THEN** the worker creates an AgentTool with the AgentDefinition and registers it in the parent agent's tool list

#### Scenario: AGENT node with missing agent_id
- **WHEN** a graph executes an AGENT node with config `{}` (no agent_id)
- **THEN** the worker returns a `WorkerResult` with an error indicating the missing agent_id

#### Scenario: AGENT node with default invocation mode
- **WHEN** a graph executes an AGENT node with config `{"agent_id": "uuid-of-agent"}` (no invocation_mode field)
- **THEN** the worker treats invocation_mode as `"graph"` and delegates to WorkflowExecutionService (backward compatible)

### Requirement: Agent-as-Tool dynamic registration
The system SHALL support an `invocation_mode` field in AGENT node configuration. When `invocation_mode: "tool"`, the target agent SHALL be registered as a callable tool in the parent agent's tool list during execution, using an `AgentDefinition` for permission scoping when provided.

#### Scenario: Agent exposed as tool with AgentDefinition
- **WHEN** an AGENT node has config `{"agent_id": "uuid-of-specialist", "invocation_mode": "tool", "agent_definition": {"tools": ["web_search"], "context_mode": "isolated"}}`
- **THEN** the parent agent's tool list includes a tool named `agent_{specialist_name}` with the AgentDefinition's tool filter and context isolation applied

#### Scenario: Agent exposed as tool without AgentDefinition (existing behavior)
- **WHEN** an AGENT node has config `{"agent_id": "uuid-of-specialist", "invocation_mode": "tool"}` (no agent_definition)
- **THEN** the parent agent's tool list includes a tool named `agent_{specialist_name}` with full tool inheritance (existing behavior unchanged)

#### Scenario: Agent tool invocation with filtered tools
- **WHEN** the parent LLM calls the `agent_{specialist_name}` tool with arguments `{"task": "analyze this data"}`
- **THEN** the system executes the specialist agent with only the tools specified in the AgentDefinition, not the specialist's full tool list

### Requirement: Context isolation per agent
The system SHALL provide isolated execution context for each agent invocation. Each agent SHALL use its own system prompt (from persona field), tools, and knowledge bases as defined in its AgentModel.

#### Scenario: Specialist agent uses own system prompt
- **WHEN** agent "billing_specialist" with persona "You are a billing expert" is invoked from agent "triage"
- **THEN** the billing_specialist's LLM invocation uses "You are a billing expert" as system prompt, NOT the triage agent's system prompt

#### Scenario: Specialist agent uses own tools
- **WHEN** agent "billing_specialist" has tools `["lookup_invoice", "process_refund"]` in its AgentModel
- **THEN** only those tools are available during the billing_specialist's execution, not the parent agent's tools

#### Scenario: Specialist agent uses own knowledge bases
- **WHEN** agent "billing_specialist" has knowledge bases `["billing_docs_kb"]` in its AgentModel
- **THEN** the billing_specialist's execution queries those knowledge bases, not the parent agent's knowledge bases
