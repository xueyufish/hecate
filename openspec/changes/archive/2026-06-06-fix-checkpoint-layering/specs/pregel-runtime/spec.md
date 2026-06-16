## MODIFIED Requirements

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
