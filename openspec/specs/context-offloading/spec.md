# context-offloading Specification

## Purpose
Context offloading preserves overflow conversation messages to the AgentEnvironment filesystem instead of discarding them, enabling the agent to retrieve dropped content via read_file on demand.

## Requirements

### Requirement: ContextOffloader preserves overflow messages to environment storage

The system SHALL provide a `ContextOffloader` class in `services/context/offloader.py` that serializes overflow messages to the agent's `AgentEnvironment` filesystem. Offloaded messages SHALL be written as JSON to `memory/sessions/{session_id}/offloaded_{timestamp}.json`, preserving full message structure (role, content, tool_calls, tool_call_id). The offloader SHALL return a compact reference stub message that replaces the offloaded block in the live context.

#### Scenario: Offload writes messages to environment file

- **WHEN** `ContextOffloader.offload(messages, session_id, environment)` is called with 30 messages and a session_id of `"s123"`
- **THEN** a JSON file SHALL be written to `memory/sessions/s123/offloaded_{timestamp}.json`
- **AND** the JSON SHALL contain the full list of 30 messages with all fields preserved
- **AND** the environment's `write_file` method SHALL be used to perform the write

#### Scenario: Offload returns compact reference stub

- **WHEN** `ContextOffloader.offload(...)` completes successfully
- **THEN** the return value SHALL be a single dict with `role: "system"`
- **AND** the `content` SHALL include the file path, a topic summary, and a `read_file` retrieval hint
- **AND** the content length SHALL NOT exceed 500 characters

#### Scenario: Offload stub includes topic summary

- **WHEN** offloaded messages contain user messages
- **THEN** the reference stub SHALL include a heuristic topic summary derived from the first 200 characters of each user message
- **AND** the total summary SHALL be truncated to 500 characters

#### Scenario: Offload stub instructs retrieval

- **WHEN** the reference stub is generated
- **THEN** the content SHALL contain the literal instruction `read_file("<path>")` pointing to the offloaded file
- **AND** the path SHALL match the actual file path written to the environment

### Requirement: ContextOffloader is disabled when no environment is available

The system SHALL gracefully skip offloading when no `AgentEnvironment` is available. The pipeline SHALL fall back to compression without writing any files.

#### Scenario: No environment in execution_context

- **WHEN** `execution_context` does not contain `"context_offloader"` or the offloader has no environment
- **THEN** the pipeline SHALL skip the offload step entirely
- **AND** the existing compression behavior SHALL proceed unchanged

#### Scenario: Offload disabled via config

- **WHEN** `CONTEXT_OFFLOAD_ENABLED` is set to `false`
- **THEN** the pipeline SHALL skip the offload step entirely
- **AND** no file writes SHALL occur

### Requirement: Offload threshold prevents trivial offloads

The system SHALL only offload when the token overflow meets or exceeds `CONTEXT_OFFLOAD_THRESHOLD_TOKENS` (default 6000). This prevents writing files for trivially small overflows.

#### Scenario: Overflow below threshold skips offload

- **WHEN** message selection drops messages totaling fewer than `CONTEXT_OFFLOAD_THRESHOLD_TOKENS` tokens
- **THEN** the pipeline SHALL skip offload
- **AND** compression SHALL proceed as the fallback

#### Scenario: Overflow at or above threshold triggers offload

- **WHEN** message selection drops messages totaling at least `CONTEXT_OFFLOAD_THRESHOLD_TOKENS` tokens
- **AND** an environment is available and offload is enabled
- **THEN** the dropped messages SHALL be offloaded to the environment

### Requirement: Offload files are named with timestamp for ordering

The system SHALL name offload files using the pattern `offloaded_{YYYYMMDDHHMMSS}.json` to enable chronological ordering without parsing file contents.

#### Scenario: Filename includes timestamp

- **WHEN** an offload file is created at 2026-07-21 14:30:22 UTC
- **THEN** the filename SHALL be `offloaded_20260721143022.json`
- **AND** later offloads in the same second SHALL append a counter suffix (e.g., `offloaded_20260721143022_1.json`)

### Requirement: Offloaded content is retrievable via read_file

The system SHALL ensure that offloaded JSON files are accessible via the agent's existing `read_file` tool. No new tool registration is required.

#### Scenario: Agent retrieves offloaded content

- **WHEN** the agent calls `read_file("memory/sessions/s123/offloaded_20260721143022.json")`
- **THEN** the environment SHALL return the JSON content as bytes
- **AND** the content SHALL parse as valid JSON containing the original message list
