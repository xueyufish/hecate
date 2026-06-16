## ADDED Requirements

### Requirement: Compile-time channel access validation
The `GraphCompiler.compile()` SHALL validate that each node's declared `channels.readable` and `channels.writable` lists reference channels that exist in the graph's `state` declaration. When a node declares access to a non-existent channel, the compiler SHALL log a WARNING. The compiler SHALL also warn when a node with no channel declaration is connected to channels via edges, suggesting that channel access be explicitly declared.

#### Scenario: Node declares readable channel that does not exist
- **WHEN** a node declares `channels.readable: ["nonexistent"]` and "nonexistent" is not in the graph's `state`
- **THEN** the compiler SHALL log a WARNING: "Node '{node_id}' declares readable channel 'nonexistent' which is not defined in graph state"

#### Scenario: Node declares writable channel that does not exist
- **WHEN** a node declares `channels.writable: ["nonexistent"]` and "nonexistent" is not in the graph's `state`
- **THEN** the compiler SHALL log a WARNING: "Node '{node_id}' declares writable channel 'nonexistent' which is not defined in graph state"

#### Scenario: Node with no channel declaration produces no warning
- **WHEN** a node has no `channels` config (neither readable nor writable)
- **THEN** the compiler SHALL NOT emit any channel access warning for that node

#### Scenario: All declared channels exist in state
- **WHEN** a node declares `channels.readable: ["messages"]` and `channels.writable: ["messages"]` and "messages" is defined in graph `state`
- **THEN** the compiler SHALL NOT emit any channel access warning for that node

### Requirement: Runtime channel access warning
The `ChannelManager.read()` and `ChannelManager.write()` methods SHALL accept an optional `node_id` parameter. When provided, the method SHALL check if the node has declared access to the channel via the compiled graph's channel access map. If the node has not declared access, a WARNING SHALL be logged.

#### Scenario: Node reads from undeclared channel
- **WHEN** `ChannelManager.read("messages", node_id="agent_a")` is called and "agent_a" has not declared "messages" in its `readable` list
- **THEN** a WARNING SHALL be logged: "Node 'agent_a' reads from channel 'messages' without declaring it as readable"

#### Scenario: Node writes to undeclared channel
- **WHEN** `ChannelManager.write("results", value, node_id="agent_b")` is called and "agent_b" has not declared "results" in its `writable` list
- **THEN** a WARNING SHALL be logged: "Node 'agent_b' writes to channel 'results' without declaring it as writable"

#### Scenario: Node reads from declared channel
- **WHEN** `ChannelManager.read("messages", node_id="agent_a")` is called and "agent_a" has declared "messages" in its `readable` list
- **THEN** no warning SHALL be logged

#### Scenario: No node_id provided skips check
- **WHEN** `ChannelManager.read("messages")` is called without `node_id`
- **THEN** no channel access check SHALL be performed

### Requirement: CompiledGraph includes channel access map
The `CompiledGraph` dataclass SHALL include a `channel_access` field mapping each node ID to its declared readable and writable channel sets. Nodes without channel declarations SHALL have empty sets.

#### Scenario: Channel access map populated from config
- **WHEN** a graph has node "agent_a" with `config.channels.readable: ["messages", "context"]` and `config.channels.writable: ["messages"]`
- **THEN** `compiled_graph.channel_access["agent_a"]` SHALL be `ChannelAccess(readable={"messages", "context"}, writable={"messages"})`

#### Scenario: Node without channel config has empty access
- **WHEN** a graph has node "condition_1" with no `channels` config
- **THEN** `compiled_graph.channel_access["condition_1"]` SHALL be `ChannelAccess(readable=set(), writable=set())`
