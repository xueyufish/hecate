## ADDED Requirements

### Requirement: SchedulerStrategy ABC defines pluggable node scheduling
The engine SHALL define a `SchedulerStrategy` ABC in `engine/scheduler.py` with abstract methods: `select_next` and `set_weights`.

#### Scenario: Select next nodes to execute
- **WHEN** `select_next(nodes, context)` is called with a list of ready node IDs
- **THEN** it SHALL return a list of node IDs in the order they should be executed

#### Scenario: Set execution weights
- **WHEN** `set_weights(weights)` is called with a dict mapping node_id to weight
- **THEN** it SHALL store the weights for use in subsequent `select_next` calls

### Requirement: FIFOScheduler preserves current sequential behavior
A `FIFOScheduler` SHALL implement SchedulerStrategy by returning nodes in their original order (first-in, first-out).

#### Scenario: FIFO ordering
- **WHEN** `select_next(["node_b", "node_a", "node_c"], {})` is called
- **THEN** it SHALL return `["node_b", "node_a", "node_c"]` (unchanged order)

#### Scenario: FIFO ignores weights
- **WHEN** `set_weights({"node_a": 10, "node_b": 1})` is called
- **THEN** subsequent `select_next` calls SHALL still return nodes in input order

#### Scenario: Empty node list
- **WHEN** `select_next([], {})` is called
- **THEN** it SHALL return `[]`

### Requirement: PregelRuntime accepts optional scheduler
PregelRuntime SHALL accept an optional `scheduler` parameter in its constructor, defaulting to `FIFOScheduler()`.

#### Scenario: Default scheduler
- **WHEN** PregelRuntime is created without a scheduler parameter
- **THEN** it SHALL use `FIFOScheduler()` internally

#### Scenario: Custom scheduler
- **WHEN** PregelRuntime is created with a custom scheduler
- **THEN** it SHALL use that scheduler for node ordering in each superstep
