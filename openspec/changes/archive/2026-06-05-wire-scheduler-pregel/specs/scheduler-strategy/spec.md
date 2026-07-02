## MODIFIED Requirements

### Requirement: PregelRuntime accepts optional scheduler
PregelRuntime SHALL accept an optional `scheduler` parameter in its constructor, defaulting to `FIFOScheduler()`.

#### Scenario: Default scheduler
- **WHEN** PregelRuntime is created without a scheduler parameter
- **THEN** it SHALL use `FIFOScheduler()` internally

#### Scenario: Custom scheduler
- **WHEN** PregelRuntime is created with a custom scheduler
- **THEN** it SHALL use that scheduler for node ordering in each superstep

#### Scenario: Explicit None
- **WHEN** PregelRuntime is created with `scheduler=None`
- **THEN** it SHALL use `FIFOScheduler()` internally (same as omitting the parameter)

## ADDED Requirements

### Requirement: PregelRuntime calls scheduler before node dispatch
At the start of each superstep, after computing `current_nodes`, PregelRuntime SHALL call `self._scheduler.select_next(current_nodes, context)` and iterate the returned order instead of the raw `current_nodes`.

#### Scenario: Scheduler called every superstep
- **WHEN** a superstep begins with `current_nodes = ["node_b", "node_a"]`
- **THEN** `scheduler.select_next(["node_b", "node_a"], context)` SHALL be called exactly once
- **AND** the runtime SHALL iterate nodes in the returned order

#### Scenario: Scheduler context includes superstep and snapshot
- **WHEN** `select_next` is called during superstep 3 with a channel snapshot `{"messages": [...]}`
- **THEN** the `context` dict SHALL contain `"superstep": 3` and `"channel_snapshot": {"messages": [...]}`

#### Scenario: Single-node superstep
- **WHEN** a superstep has only one node (`current_nodes = ["only_node"]`)
- **THEN** `select_next` SHALL still be called with `["only_node"]`
- **AND** the result SHALL be used for iteration (even though order is trivial)

#### Scenario: Empty current_nodes
- **WHEN** a superstep has no nodes (`current_nodes = []`)
- **THEN** `select_next` SHALL NOT be called (the while-loop condition prevents entering the superstep body)

### Requirement: Scheduler receives FAN_OUT and MERGE nodes transparently
The scheduler SHALL receive all node IDs in `current_nodes`, including FAN_OUT and MERGE typed nodes. Special handling for these node types occurs inside the dispatch loop, after scheduling.

#### Scenario: FAN_OUT node in current_nodes
- **WHEN** `current_nodes` contains a FAN_OUT node ID
- **THEN** `select_next` SHALL receive it like any other node
- **AND** the loop body SHALL dispatch it via `_dispatch_fan_out()` as usual

#### Scenario: MERGE node in current_nodes
- **WHEN** `current_nodes` contains a MERGE node ID
- **THEN** `select_next` SHALL receive it like any other node
- **AND** the loop body SHALL execute it via `_execute_merge()` as usual
