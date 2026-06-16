## Requirements

### Requirement: Pregel runtime executes superstep loop
The `PregelRuntime.execute()` SHALL execute a compiled graph in superstep cycles until termination. When a FAN_OUT node is encountered, the runtime SHALL dispatch all branch nodes concurrently via `asyncio.gather` and collect results before advancing to the MERGE node.

#### Scenario: Linear execution
- **WHEN** a graph with nodes A→B→C→__end__ is executed
- **THEN** the runtime SHALL execute A, then B, then C, yielding events after each superstep

#### Scenario: Max superstep guard
- **WHEN** execution exceeds `max_supersteps` (default 100)
- **THEN** the runtime SHALL raise `RuntimeError` with message indicating possible infinite loop

#### Scenario: Fan-out parallel execution
- **WHEN** a FAN_OUT node with branches ["analyst_a", "analyst_b", "analyst_c"] is encountered
- **THEN** the runtime SHALL dispatch all 3 branch workers concurrently via `asyncio.gather`
- **AND** each branch SHALL write its result to an isolated sub-channel `_fanout__{fan_out_id}__{branch_id}`

#### Scenario: Merge aggregation after fan-out
- **WHEN** all branches of a FAN_OUT have completed and the MERGE node is the next node
- **THEN** the MERGE worker SHALL read all branch sub-channels and produce an aggregated dict output

#### Scenario: Fan-out branch failure propagates
- **WHEN** one branch of a FAN_OUT fails (raises an exception)
- **THEN** the entire fan-out execution SHALL fail and the error SHALL propagate to the caller

### Requirement: Interrupt/resume via checkpoint
The runtime SHALL support interrupt/resume by persisting full state to checkpoint on interrupt and restoring on resume. This behavior SHALL be preserved for graphs containing FAN_OUT/MERGE nodes. The `PostgresCheckpointStore` concrete implementation SHALL reside in the services layer (`services/checkpoint_store.py`), NOT in the engine layer.

#### Scenario: Worker triggers interrupt
- **WHEN** a worker returns `Command(interrupt=value)`
- **THEN** the runtime SHALL save a checkpoint with interrupt metadata, yield `{"type": "interrupt", "value": value}`, and stop execution

#### Scenario: Resume from interrupt
- **WHEN** `execute()` is called with `resume_value`
- **THEN** the runtime SHALL restore from the last checkpoint, write `resume_value` to the `_resume_value` channel, and continue from the node after the interrupt point

#### Scenario: Engine layer has no PostgresCheckpointStore
- **WHEN** examining `engine/checkpoint.py`
- **THEN** it SHALL contain only `CheckpointStore` ABC and `InMemoryCheckpointStore`
- **AND** it SHALL NOT import from `models/`, `services/`, or `sqlalchemy`

#### Scenario: PostgresCheckpointStore lives in services
- **WHEN** production code needs a persistent checkpoint store
- **THEN** it SHALL import `PostgresCheckpointStore` from `hecate.services.checkpoint_store`
- **AND** the constructor SHALL accept `session_factory: async_sessionmaker[AsyncSession]`

### Requirement: Multi-key edge resolution
The `_resolve_next_nodes` method SHALL support multi-key conditional routing by looking up the `_route` value in the edge target dict, with fallback to "default" and then "false".

#### Scenario: Multi-key routing
- **WHEN** `_route` is "finance" and edge target is `{"finance": "fin_agent", "tech": "tech_agent", "default": "general_agent"}`
- **THEN** execution SHALL route to "fin_agent"

#### Scenario: Default fallback
- **WHEN** `_route` is "unknown" and edge target has "default" key
- **THEN** execution SHALL route to the "default" target

#### Scenario: Legacy false fallback
- **WHEN** `_route` is "unknown" and edge target has no "default" but has "false" key
- **THEN** execution SHALL route to the "false" target
