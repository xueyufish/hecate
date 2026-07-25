# tool-decision-log Specification

## Purpose

Define the ToolDecision data model, async batch write pipeline, emission points from policy evaluation, REST API for querying decisions, configurable retention, and backward-compatible configuration aliases.

## Requirements

### Requirement: ToolDecisionModel data model

The system SHALL provide a `ToolDecisionModel` ORM table (`tool_decisions`) that stores structured tool policy decision events. Each event SHALL capture: agent_id, workspace_id, session_id (nullable), tool_name, arguments_hash (SHA-256), decision, reason, policy_version, on_behalf_of_user (nullable), timestamp, and per-layer decision breakdown.

#### Scenario: Decision event created on policy evaluation

- **WHEN** `ToolPolicyPipeline.evaluate_execution()` returns a decision for tool `bash`
- **THEN** a `ToolDecisionModel` row is created with the tool name, final decision, reason, and per-layer results

#### Scenario: Arguments stored as hash not raw

- **WHEN** a tool call with arguments `{"command": "rm -rf /tmp/data"}` is evaluated
- **THEN** the decision event stores `arguments_hash` as SHA-256 of the arguments
- **AND** raw arguments are NOT stored in the decision table

#### Scenario: Policy version recorded

- **WHEN** a policy evaluation occurs
- **THEN** the decision event records `policy_version` as a hash of the effective policy configuration at evaluation time

### Requirement: Async batch write for decision events

The system SHALL buffer tool decision events in memory and flush to the database in batches (every `AGENT_ENV_DECISION_BATCH_SIZE` events or `AGENT_ENV_DECISION_FLUSH_INTERVAL` seconds, whichever comes first).

#### Scenario: Events buffered until threshold

- **WHEN** 30 decision events are generated within the flush interval
- **THEN** events remain in the in-memory buffer (not yet written to database)

#### Scenario: Flush on event count threshold

- **WHEN** the batch size threshold is reached
- **THEN** all buffered events are flushed to the database in a single batch write
- **AND** the buffer is cleared

#### Scenario: Flush on time threshold

- **WHEN** the flush interval elapses and the buffer has pending events
- **THEN** all events are flushed to the database
- **AND** the buffer is cleared

#### Scenario: Flush on graceful shutdown

- **WHEN** the application receives a shutdown signal
- **THEN** all buffered events are flushed before shutdown completes

### Requirement: Decision event emission from policy evaluations

The system SHALL automatically emit tool decision events from three emission points: `ToolPolicyPipeline.evaluate_visibility()`, `ToolPolicyPipeline.evaluate_execution()`, and `ToolAccessPolicy.evaluate()`.

#### Scenario: Visibility evaluation emits per-tool event

- **WHEN** `evaluate_visibility()` filters out a tool (HIDE or DENY)
- **THEN** a decision event is emitted with the tool name, layer that caused hiding, and decision

#### Scenario: Execution evaluation emits final decision event

- **WHEN** `evaluate_execution()` returns a final decision with per-layer results
- **THEN** a decision event is emitted with the final decision, reason, and all layer results

#### Scenario: ToolAccessPolicy emits access decision event

- **WHEN** `ToolAccessPolicy.evaluate()` returns `REQUIRE_APPROVAL`
- **THEN** a decision event is emitted with the access decision, matched rule, and risk level

### Requirement: REST API for decision event query

The system SHALL expose a REST API endpoint at `GET /api/security/decisions` for querying tool decision events with filtering by agent_id, workspace_id, session_id, decision, tool_name, and time range.

#### Scenario: Query by agent

- **WHEN** a client requests `GET /api/security/decisions?agent_id={agent_id}`
- **THEN** the system returns all decision events for that agent within the default time window

#### Scenario: Query by decision

- **WHEN** a client requests `GET /api/security/decisions?decision=DENY`
- **THEN** the system returns only decision events where the decision was DENY

#### Scenario: Query by time range

- **WHEN** a client requests `GET /api/security/decisions?start=...&end=...`
- **THEN** the system returns only events within the specified time range

#### Scenario: Pagination

- **WHEN** a client requests `GET /api/security/decisions?limit=50&offset=100`
- **THEN** the system returns 50 events starting from offset 100

### Requirement: Configurable retention with auto-cleanup

The system SHALL automatically delete decision events older than the configured retention period (`AGENT_ENV_DECISION_RETENTION_DAYS`, default 30 days).

#### Scenario: Default retention is 30 days

- **WHEN** `AGENT_ENV_DECISION_RETENTION_DAYS` is not set
- **THEN** events older than 30 days are eligible for cleanup

#### Scenario: Decision logging disabled

- **WHEN** `AGENT_ENV_DECISION_ENABLED=false`
- **THEN** no `ToolDecisionModel` rows are created
- **AND** policy evaluations proceed without decision logging overhead

### Requirement: Backward-compatible config aliases

The system SHALL accept legacy config keys (`AGENT_ENV_AUDIT_*`) as aliases for the new keys (`AGENT_ENV_DECISION_*`). When both are set, the new key takes precedence.

#### Scenario: Legacy config key works

- **WHEN** `.env` contains `AGENT_ENV_AUDIT_ENABLED=true` but not `AGENT_ENV_DECISION_ENABLED`
- **THEN** the system enables decision logging as if `AGENT_ENV_DECISION_ENABLED=true` was set

#### Scenario: New key takes precedence

- **WHEN** `.env` contains both `AGENT_ENV_AUDIT_ENABLED=false` and `AGENT_ENV_DECISION_ENABLED=true`
- **THEN** the system enables decision logging (new key wins)
