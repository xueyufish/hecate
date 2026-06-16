## ADDED Requirements

### Requirement: Evidence Tracker captures tool call results
The system SHALL intercept tool execution results and store them as structured evidence records with normalized content, source metadata, and importance scoring.

#### Scenario: Successful tool result captured
- **WHEN** a tool call returns a result string "Executed web_search with args {'query': 'Hecate architecture'}"
- **THEN** the evidence tracker SHALL create an evidence record containing: tool name, original arguments, normalized result content, execution timestamp, and source type "tool"

#### Scenario: Error tool result captured
- **WHEN** a tool call fails with an exception
- **THEN** the evidence tracker SHALL create an evidence record with the error message as content, flagged with `is_error=True`, and importance score of 0

### Requirement: Evidence normalization transforms raw tool output
The system SHALL normalize raw tool output into a structured format with consistent fields regardless of the source tool.

#### Scenario: JSON tool output normalization
- **WHEN** a tool returns a JSON string `{"status": "ok", "data": [1, 2, 3]}`
- **THEN** the evidence tracker SHALL parse the JSON and store the structured data as the normalized content

#### Scenario: Plain text tool output normalization
- **WHEN** a tool returns a plain text string
- **THEN** the evidence tracker SHALL store the text as-is in normalized content with a metadata flag `format: "text"`

### Requirement: Evidence provenance tracking
The system SHALL track the provenance chain for each evidence record: which tool produced it, which arguments were used, which conversation turn it belongs to, and which message it is associated with.

#### Scenario: Full provenance chain
- **WHEN** an evidence record is created
- **THEN** it SHALL contain: `tool_name`, `tool_arguments`, `session_id`, `conversation_id`, `message_id`, `turn_index`, and `created_at`

### Requirement: Evidence importance scoring
The system SHALL assign an importance score (0.0 to 1.0) to each evidence record based on configurable rules.

#### Scenario: Default importance scoring
- **WHEN** an evidence record is created and no custom scoring rules are configured
- **THEN** the tracker SHALL assign a default score of 0.5

#### Scenario: Error results get zero importance
- **WHEN** an evidence record has `is_error=True`
- **THEN** the importance score SHALL be 0.0 regardless of custom rules

#### Scenario: Evidence referenced in subsequent turns gets boosted
- **WHEN** an evidence record is referenced (included in context) in a subsequent conversation turn
- **THEN** its importance score SHALL be incremented by 0.1, capped at 1.0

### Requirement: Evidence storage and retrieval
The system SHALL persist evidence records in the database and provide query interfaces for retrieval by session, tool type, time range, and importance threshold.

#### Scenario: Query evidence by session
- **WHEN** a query is made for all evidence in a given session
- **THEN** the tracker SHALL return all evidence records for that session ordered by creation time

#### Scenario: Query evidence by importance threshold
- **WHEN** a query is made for evidence with importance >= 0.7
- **THEN** the tracker SHALL return only evidence records meeting the threshold
