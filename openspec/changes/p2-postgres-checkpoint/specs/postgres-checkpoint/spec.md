## ADDED Requirements

### Requirement: Save checkpoint to PostgreSQL
The system SHALL persist checkpoints to PostgreSQL using the existing CheckpointModel ORM.

#### Scenario: Save checkpoint
- **WHEN** `save()` is called with session_id, superstep, node_id, channel_state
- **THEN** the system creates a CheckpointModel record and returns the checkpoint ID

#### Scenario: Save with metadata
- **WHEN** `save()` is called with metadata (e.g., interrupt info)
- **THEN** the metadata SHALL be stored in the metadata JSONB column

### Requirement: Load checkpoint from PostgreSQL
The system SHALL load checkpoints from PostgreSQL, supporting both latest and specific checkpoint retrieval.

#### Scenario: Load latest checkpoint
- **WHEN** `load(session_id)` is called without checkpoint_id
- **THEN** the system returns the checkpoint with the highest superstep for that session

#### Scenario: Load specific checkpoint
- **WHEN** `load(session_id, checkpoint_id)` is called
- **THEN** the system returns the exact checkpoint matching that ID

#### Scenario: No checkpoint found
- **WHEN** `load()` is called for a session with no checkpoints
- **THEN** the system returns None

### Requirement: List checkpoints
The system SHALL list checkpoints for a session ordered by superstep descending.

#### Scenario: List with limit
- **WHEN** `list_checkpoints(session_id, limit=10)` is called
- **THEN** the system returns the 10 most recent checkpoints

### Requirement: Memory cache for hot path
The system SHALL cache the most recent checkpoint per session in memory.

#### Scenario: Cache hit on load
- **WHEN** `load()` is called for a session that was recently saved
- **THEN** the system returns the cached checkpoint without querying the database

#### Scenario: Cache invalidation on save
- **WHEN** `save()` is called for a session
- **THEN** the cache for that session SHALL be updated with the new checkpoint
