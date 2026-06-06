## Requirements

### Requirement: Broadcast pipeline factory function
The system SHALL provide a `build_broadcast_pipeline()` factory function in `engine/templates.py` that accepts a list of participant definitions and returns a `GraphConfig` representing a sequential round-robin graph where all participants share the same `messages` TOPIC channel.

#### Scenario: Basic three-participant broadcast
- **WHEN** `build_broadcast_pipeline(participants=[{"id": "alice", "model": "gpt-4o", "system_prompt": "..."}, {"id": "bob", "model": "gpt-4o", "system_prompt": "..."}, {"id": "charlie", "model": "gpt-4o", "system_prompt": "..."}])` is called
- **THEN** the returned GraphConfig SHALL contain 3 AGENT nodes (alice, bob, charlie), all reading from and writing to the same `messages` TOPIC channel, with sequential edges forming alice→bob→charlie→__end__

#### Scenario: Shared message visibility
- **WHEN** a broadcast pipeline is created with N participants
- **THEN** the `messages` TOPIC channel SHALL be readable and writable by ALL participants, ensuring each participant sees all messages from all previous participants

#### Scenario: Broadcast edge connectivity
- **WHEN** a broadcast pipeline is created with N participants
- **THEN** edges SHALL form a linear chain: `__start__` → participant_0 → participant_1 → ... → participant_{N-1} → `__end__`, and the `entry` field SHALL be set to participant_0

### Requirement: Broadcast pipeline with moderator
The system SHALL support an optional moderator in `build_broadcast_pipeline()` that participates before and after the broadcast round, providing initial context and final summary.

#### Scenario: Broadcast with moderator enabled
- **WHEN** `build_broadcast_pipeline(participants=[...], moderator={"model": "gpt-4o", "system_prompt": "You are a moderator."})` is called
- **THEN** the graph SHALL contain an additional `moderator` AGENT node at the beginning and end: `__start__` → moderator → participant_0 → ... → participant_{N-1} → moderator → `__end__`, and the `entry` field SHALL be set to `moderator`

#### Scenario: Broadcast without moderator
- **WHEN** `build_broadcast_pipeline(participants=[...])` is called without a moderator
- **THEN** the graph SHALL contain only participant nodes with no moderator node

### Requirement: Broadcast participant validation
The system SHALL validate participant definitions when building a broadcast pipeline.

#### Scenario: Minimum participant count
- **WHEN** `build_broadcast_pipeline(participants=[single_participant])` is called with fewer than 2 participants
- **THEN** the function SHALL raise `ValueError` with a descriptive message

#### Scenario: Duplicate participant IDs rejected
- **WHEN** `build_broadcast_pipeline(participants=[{"id": "agent", ...}, {"id": "agent", ...}])` is called with duplicate participant IDs
- **THEN** the function SHALL raise `ValueError`

### Requirement: Broadcast pipeline JSON template
The system SHALL include a `broadcast-pipeline.json` template file in `data/orchestration_templates/` demonstrating a 3-participant round-robin broadcast with a moderator.

#### Scenario: Template loads successfully
- **WHEN** the broadcast-pipeline template is loaded via the orchestration-templates API
- **THEN** the template SHALL contain 3 participant AGENT nodes, 1 moderator AGENT node, a `messages` TOPIC channel shared by all, and sequential edges forming a round-robin with moderator at start and end
