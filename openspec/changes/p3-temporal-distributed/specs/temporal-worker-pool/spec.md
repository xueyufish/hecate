## ADDED Requirements

### Requirement: TemporalWorkerPool dispatches nodes as Activities
The system SHALL provide a TemporalWorkerPool that dispatches graph node execution as Temporal Activities.

#### Scenario: Dispatch node execution
- **WHEN** PregelRuntime dispatches a node via TemporalWorkerPool
- **THEN** the system schedules a Temporal Activity and waits for result

#### Scenario: Activity timeout
- **WHEN** an Activity exceeds its timeout
- **THEN** the system returns timeout error and Pregel handles retry

### Requirement: TemporalWorkerPool supports heartbeat
The system SHALL support Activity heartbeats for long-running node executions.

#### Scenario: Heartbeat during execution
- **WHEN** a node execution takes more than 30 seconds
- **THEN** the Activity sends heartbeat signals to Temporal

#### Scenario: Heartbeat timeout
- **WHEN** heartbeat is not received for 60 seconds
- **THEN** Temporal considers the Activity failed and retries
