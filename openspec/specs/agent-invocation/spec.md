## Purpose
Define how agents are invoked in the execution graph, including direct agent execution via EnginePort, AGENT node handling in workers, and Agent-as-Tool dynamic registration for hierarchical delegation.
## Requirements
### Requirement: EnginePort agent_execute method
The system SHALL add an `agent_execute` method to `EnginePort` that accepts an agent_id, messages, channel_snapshot, and optional context, and returns a dict containing the agent's response.

#### Scenario: Agent execution via port
- **WHEN** `agent_execute(agent_id=UUID("..."), messages=[{"role": "user", "content": "hello"}], channel_snapshot={})` is called
- **THEN** the port resolves the AgentModel by ID, builds isolated context from the agent's persona/tools/knowledge bases, invokes the LLM, and returns `{"response": "...", "usage": {...}}`

#### Scenario: Agent not found
- **WHEN** `agent_execute(agent_id=UUID("nonexistent"), ...)` is called
- **THEN** the port raises `ValueError` with message indicating the agent was not found

### Requirement: AGENT node real execution
The system SHALL execute AGENT-type nodes by calling `EnginePort.agent_execute()` with the agent_id from the node configuration. The agent's response SHALL be written to the `messages` channel.

#### Scenario: AGENT node with valid agent_id
- **WHEN** a graph executes an AGENT node with config `{"agent_id": "uuid-of-agent", "channels": {"readable": ["messages"], "writable": ["messages"]}}`
- **THEN** the worker calls `port.agent_execute(agent_id, messages_from_channel, channel_snapshot)` and writes the response to the `messages` channel

#### Scenario: AGENT node with missing agent_id
- **WHEN** a graph executes an AGENT node with config `{}` (no agent_id)
- **THEN** the worker returns a `WorkerResult` with an error indicating the missing agent_id

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

