## ADDED Requirements

### Requirement: Handoff tool injection at agent execute time
When an AGENT node has outgoing edges with `trigger` of `"handoff"` or `"dynamic_handoff"`, the `AgentExecutionPort.agent_execute()` SHALL inject the `handoff_to_agent` tool into the LLM's tool list before invocation. The tool SHALL accept a `target` parameter whose `enum` is the list of valid target node IDs reachable via those edges. The tool SHALL NOT be injected if no such edges exist.

#### Scenario: Handoff tool injected with single static target
- **WHEN** an AGENT node "router" has one outgoing edge `{"source": "router", "target": "billing", "trigger": "handoff"}`
- **AND** `agent_execute` is called for "router"
- **THEN** the LLM receives a tool named `handoff_to_agent` with `parameters.properties.target.enum` equal to `["billing"]`

#### Scenario: Handoff tool injected with multiple dynamic targets
- **WHEN** an AGENT node "triage" has one outgoing edge `{"source": "triage", "target": {"billing": "billing_agent", "tech": "tech_agent"}, "trigger": "dynamic_handoff"}`
- **AND** `agent_execute` is called for "triage"
- **THEN** the LLM receives the `handoff_to_agent` tool with `target.enum` equal to `["billing_agent", "tech_agent"]` (the dict values, deduplicated)

#### Scenario: No handoff edges yields no handoff tool
- **WHEN** an AGENT node has no outgoing `handoff` or `dynamic_handoff` edges
- **THEN** `agent_execute` SHALL NOT inject the `handoff_to_agent` tool

#### Scenario: Handoff targets come from execution_context
- **WHEN** `PregelRuntime._dispatch_node` invokes a worker for a node that has outgoing handoff edges
- **THEN** the worker's `execution_context` SHALL contain a `handoff_targets` key holding a list of `{"node_id": str, "description": str}` objects, one per reachable target

### Requirement: Handoff tool call detection and Command production
When the LLM's response contains a tool call to `handoff_to_agent` with a valid `target`, the `AgentExecutionPort.agent_execute()` SHALL return a result dict containing `handoff_to: <target_node_id>`. The `AgentWorker` SHALL translate this into a `WorkerResult` whose `command` is `Command(goto=<target_node_id>)`.

#### Scenario: Valid handoff target produces Command(goto=...)
- **WHEN** the LLM calls `handoff_to_agent(target="tech_agent")` and `"tech_agent"` is in the valid target list
- **THEN** `agent_execute` returns `{"response": "", "handoff_to": "tech_agent"}`
- **AND** `AgentWorker.execute` returns `WorkerResult(command=Command(goto="tech_agent"))`

#### Scenario: Invalid handoff target is rejected
- **WHEN** the LLM calls `handoff_to_agent(target="unknown")` and `"unknown"` is NOT in the valid target list
- **THEN** the port SHALL return an error response to the LLM indicating the target is invalid, prompting a retry
- **AND** no `Command(goto=...)` SHALL be produced

#### Scenario: PregelRuntime honors the Command
- **WHEN** a `WorkerResult(command=Command(goto="tech_agent"))` is returned to `PregelRuntime`
- **THEN** the next superstep SHALL execute the "tech_agent" node

### Requirement: Per-target handoff tool descriptions
The `handoff_to_agent` tool's description SHALL include each candidate target's role so the LLM can route accurately. The description for each target SHALL be sourced, in priority order: (1) the source AGENT node's `handoff.description` override; (2) the target Agent's `AgentModel.description`; (3) the target AGENT node's `name`.

#### Scenario: Description uses node-level override first
- **WHEN** the source AGENT node config contains `{"handoff": {"description": "Specialist agents for the support desk"}}`
- **THEN** the `handoff_to_agent` tool description SHALL incorporate "Specialist agents for the support desk"

#### Scenario: Description falls back to target AgentModel description
- **WHEN** no `handoff.description` override exists on the source node
- **AND** the target agent's `AgentModel.description` is "Handles billing inquiries"
- **THEN** the tool description SHALL incorporate "Handles billing inquiries" for that target

#### Scenario: Description falls back to node name when nothing else is available
- **WHEN** neither `handoff.description` nor `AgentModel.description` is set for a target
- **THEN** the tool description SHALL use the target AGENT node's `name`

### Requirement: Context mode for handoff
The system SHALL support three context-passing strategies for the downstream agent after a handoff, controlled by the optional `handoff.context_mode` field on the source AGENT node config. Valid values are `"inherited"` (default), `"isolated"`, and `"summarized"`.

#### Scenario: Inherited mode passes full message history
- **WHEN** an AGENT node has `handoff.context_mode == "inherited"` (or the field is absent)
- **AND** a handoff to target "tech_agent" occurs
- **THEN** the `messages` channel write for the handoff SHALL include the full message history as it was at handoff time, followed by the AIMessage + ToolMessage pair described in the "AIMessage + ToolMessage pairing on handoff" requirement

#### Scenario: Isolated mode starts fresh
- **WHEN** an AGENT node has `handoff.context_mode == "isolated"`
- **AND** a handoff occurs
- **THEN** the `messages` channel write SHALL contain only: (a) the AIMessage + ToolMessage pair, and (b) a single system note `"Handed off from {source_node_id}"` — no prior history

#### Scenario: Summarized mode collapses history
- **WHEN** an AGENT node has `handoff.context_mode == "summarized"`
- **AND** a handoff occurs
- **THEN** the `messages` channel write SHALL contain: (a) a single `system` message with a structured summary (`from`, `intent`, `key_facts`, `open_questions`), and (b) the AIMessage + ToolMessage pair

#### Scenario: Invalid context_mode is rejected by the compiler
- **WHEN** the compiler encounters an AGENT node with `handoff.context_mode` set to a value other than `"inherited"`, `"isolated"`, or `"summarized"`
- **THEN** compilation SHALL fail with a `GraphValidationError` describing the invalid value

#### Scenario: Default context_mode is inherited
- **WHEN** an AGENT node has no `handoff.context_mode` field
- **THEN** the runtime SHALL treat the mode as `"inherited"`

### Requirement: AIMessage + ToolMessage pairing on handoff
When a handoff occurs, the `messages` channel update SHALL include exactly one `AIMessage` (the LLM's tool-call message, with the original `tool_call_id` preserved) paired with exactly one `ToolMessage` whose `tool_call_id` matches the AIMessage and whose content is `"Handed off to {target_node_id}"`. No unpaired tool-call messages SHALL be written.

#### Scenario: Pairing produces valid conversation history
- **WHEN** the LLM calls `handoff_to_agent` with `tool_call_id="call_abc123"`
- **THEN** the resulting `messages` channel update SHALL contain an `AIMessage` with `tool_calls=[{"id": "call_abc123", ...}]` and a `ToolMessage` with `tool_call_id="call_abc123"`

#### Scenario: Tool-call ID preserved exactly
- **WHEN** the LLM provider returns a `tool_call_id` of `"call_xyz"`
- **THEN** the same `"call_xyz"` SHALL appear on both the AIMessage and the ToolMessage in the channel update

#### Scenario: Collision on duplicate tool_call_id generates a UUID suffix
- **WHEN** the LLM provider returns the same `tool_call_id` for two consecutive handoff calls (rare edge case)
- **THEN** the second occurrence SHALL be renamed to `"{original_id}-{uuid4_hex[:8]}"` on both the AIMessage and the ToolMessage
- **AND** a WARNING SHALL be logged

### Requirement: PregelRuntime populates handoff_targets in execution_context
Before dispatching a worker for an AGENT node, `PregelRuntime` SHALL inspect the compiled graph's outgoing edges and populate `execution_context["handoff_targets"]` with a list of `{"node_id": str, "description": str}` objects for each edge whose `trigger` is `"handoff"` or `"dynamic_handoff"`. The list SHALL be empty (or the key absent) when no such edges exist.

#### Scenario: Single static handoff edge populates one target
- **WHEN** PregelRuntime dispatches a worker for node "router" which has one outgoing edge `{"source": "router", "target": "billing", "trigger": "handoff"}`
- **THEN** `execution_context["handoff_targets"]` SHALL equal `[{"node_id": "billing", "description": <resolved description>}]`

#### Scenario: Dynamic handoff edge populates all dict values
- **WHEN** PregelRuntime dispatches a worker for node "triage" which has one outgoing edge `{"source": "triage", "target": {"billing": "billing_agent", "tech": "tech_agent"}, "trigger": "dynamic_handoff"}`
- **THEN** `execution_context["handoff_targets"]` SHALL contain two entries: `{"node_id": "billing_agent", ...}` and `{"node_id": "tech_agent", ...}`

#### Scenario: No handoff edges yields empty list
- **WHEN** PregelRuntime dispatches a worker for a node with no outgoing handoff or dynamic_handoff edges
- **THEN** `execution_context["handoff_targets"]` SHALL be an empty list (or the key SHALL be absent)

#### Scenario: Non-AGENT nodes do not receive handoff_targets
- **WHEN** PregelRuntime dispatches a worker for a CONVERSATION, CONDITION, or other non-AGENT node type
- **THEN** `execution_context["handoff_targets"]` SHALL NOT be populated regardless of outgoing edges
- **NOTE** This restriction scopes this change to AGENT nodes; CONVERSATION handoff is a separate future change.
