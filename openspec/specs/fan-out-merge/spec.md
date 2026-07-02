## ADDED Requirements

### Requirement: FAN_OUT node dispatches parallel branches
The FAN_OUT NodeType SHALL represent a parallel dispatch point that splits execution into multiple concurrent branches. The node config SHALL contain a `branches` field listing the node IDs of all parallel branch targets.

#### Scenario: FAN_OUT node configuration
- **WHEN** a node has type FAN_OUT with config `{"branches": ["analyst_a", "analyst_b", "analyst_c"]}`
- **THEN** the runtime SHALL dispatch all 3 branch nodes concurrently

#### Scenario: FAN_OUT node has no worker execution
- **WHEN** the runtime encounters a FAN_OUT node
- **THEN** the FAN_OUT node itself SHALL NOT invoke a worker — it only triggers dispatch of its branch nodes

### Requirement: MERGE node collects parallel branch results
The MERGE NodeType SHALL represent an aggregation point that collects results from all branches of a preceding FAN_OUT. The node config SHALL contain a `fan_out_source` field referencing the FAN_OUT node ID and an `output_channel` field specifying where to write the aggregated result.

#### Scenario: MERGE collects all branch outputs
- **WHEN** a MERGE node executes after a FAN_OUT with 3 branches
- **THEN** the MERGE node SHALL read all branch sub-channels and write a dict `{branch_id: result}` to the output channel

#### Scenario: MERGE waits for all branches
- **WHEN** a MERGE node is reached but not all branches have completed
- **THEN** the MERGE node SHALL wait until all branch results are available before producing output

### Requirement: FAN_OUT branch sub-channel isolation
Each branch of a FAN_OUT SHALL write to an isolated sub-channel named `_fanout__{fan_out_node_id}__{branch_node_id}` to prevent parallel branches from overwriting each other's state.

#### Scenario: Branch writes to sub-channel
- **WHEN** branch node "analyst_a" executes as part of FAN_OUT node "fanout_1"
- **THEN** the branch result SHALL be written to sub-channel `_fanout__fanout_1__analyst_a`

#### Scenario: Main channel state preserved
- **WHEN** parallel branches execute concurrently
- **THEN** the main "messages" channel SHALL NOT be modified by individual branches — only the MERGE node writes the aggregated result
