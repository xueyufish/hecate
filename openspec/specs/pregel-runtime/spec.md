## ADDED Requirements

### Requirement: Pregel runtime executes superstep loop
The `PregelRuntime.execute()` SHALL execute a compiled graph in superstep cycles until termination.

#### Scenario: Linear execution
- **WHEN** a graph with nodes A→B→C→__end__ is executed
- **THEN** the runtime SHALL execute A, then B, then C, yielding events after each superstep

#### Scenario: Max superstep guard
- **WHEN** execution exceeds `max_supersteps` (default 100)
- **THEN** the runtime SHALL raise `RuntimeError` with message indicating possible infinite loop

### Requirement: Interrupt/resume via checkpoint
The runtime SHALL support interrupt/resume by persisting full state to checkpoint on interrupt and restoring on resume.

#### Scenario: Worker triggers interrupt
- **WHEN** a worker returns `Command(interrupt=value)`
- **THEN** the runtime SHALL save a checkpoint with interrupt metadata, yield `{"type": "interrupt", "value": value}`, and stop execution

#### Scenario: Resume from interrupt
- **WHEN** `execute()` is called with `resume_value`
- **THEN** the runtime SHALL restore from the last checkpoint, write `resume_value` to the `_resume_value` channel, and continue from the node after the interrupt point

#### Scenario: Interrupt with conditional edge
- **WHEN** resuming from an interrupt where the interrupted node has a conditional edge
- **THEN** the runtime SHALL use the `_route` key from `interrupt_updates` to select the correct branch, defaulting to "true"

### Requirement: Channel isolation via deep copy
All channel reads SHALL return deep copies to guarantee isolation between workers in the same superstep.

#### Scenario: Worker reads channel
- **WHEN** a worker reads a channel value
- **THEN** it SHALL receive a deep copy; mutations to the copy SHALL NOT affect other workers or the engine state

### Requirement: StreamMode controls yielded events
The runtime SHALL yield different event types based on `StreamMode`.

#### Scenario: UPDATES mode
- **WHEN** `stream_mode=StreamMode.UPDATES`
- **THEN** the runtime SHALL yield `{"type": "update", "node": node_id, "output": channel_updates}` per worker

#### Scenario: VALUES mode
- **WHEN** `stream_mode=StreamMode.VALUES`
- **THEN** the runtime SHALL yield `{"type": "values", "state": full_channel_snapshot}` after each superstep

### Requirement: WorkerPool abstract interface
The `WorkerPool` SHALL define a `dispatch()` method for running workers.

#### Scenario: DirectWorkerPool default
- **WHEN** no pool is specified
- **THEN** `DirectWorkerPool` SHALL be used, which awaits each worker directly in the current event loop

### Requirement: Subgraph execution with channel mapping
The `execute_subgraph()` function SHALL bridge parent-child channels, run a child graph in isolation, and propagate final state back.

#### Scenario: Default channel mapping
- **WHEN** no `channel_mapping` is provided
- **THEN** it SHALL default to `{"messages": "messages", "context": "context"}`

#### Scenario: State propagation
- **WHEN** the sub-graph completes
- **THEN** only the final VALUES event state SHALL be written back to parent channels via the mapping

### Requirement: CheckpointStore abstract interface
The `CheckpointStore` SHALL define save, load, and list_checkpoints methods.

#### Scenario: InMemoryCheckpointStore for testing
- **WHEN** `InMemoryCheckpointStore` is used
- **THEN** it SHALL store checkpoints in a dict with dual storage: full history list and latest-only cache

#### Scenario: PostgresCheckpointStore for production
- **WHEN** `PostgresCheckpointStore` is used
- **THEN** it SHALL persist to `checkpoints` table via SQLAlchemy with an LRU cache (default 128 sessions) for hot-path acceleration

#### Scenario: Load latest checkpoint
- **WHEN** `load(session_id)` is called without `checkpoint_id`
- **THEN** it SHALL return the most recent checkpoint for that session (cache first, then DB)
