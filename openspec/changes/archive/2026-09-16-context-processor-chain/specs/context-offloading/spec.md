## ADDED Requirements

### Requirement: Recall tool reloads offloaded content into context

The system SHALL expose a `recall` tool to agents whose sessions have offloaded context blocks. Invoking `recall` with an offload file path SHALL return the full offloaded message block (all fields preserved) as tool result content, making it available in-context for subsequent reasoning. Recall SHALL be read-only with respect to conversation state: it SHALL NOT modify the channel message history, checkpoint data, or the offloaded files themselves.

#### Scenario: Recall returns full offloaded block

- **WHEN** the agent invokes `recall` with the path of an existing offload file
- **THEN** the tool SHALL return the complete offloaded message block as content, with roles, content, tool_calls, and tool_call_id fields preserved

#### Scenario: Recall with unknown path

- **WHEN** the agent invokes `recall` with a path that does not exist in the environment
- **THEN** the tool SHALL return an error result identifying the missing path
- **AND** no file writes SHALL occur

#### Scenario: Recall does not mutate conversation state

- **WHEN** a recall tool call completes successfully
- **THEN** the channel message history SHALL contain only the original messages plus the assistant turn that invoked recall and the recall tool result
- **AND** the offloaded file SHALL remain unchanged in the environment
