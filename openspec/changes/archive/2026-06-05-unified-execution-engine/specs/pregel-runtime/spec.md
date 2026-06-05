## ADDED Requirements

### Requirement: StreamMode.MESSAGES support in PregelRuntime
PregelRuntime SHALL support a third StreamMode (MESSAGES) that yields individual token events from streaming Workers during the dispatch phase.

#### Scenario: MESSAGES mode yields token events
- **WHEN** PregelRuntime executes with `stream_mode=StreamMode.MESSAGES`
- **AND** a Worker yields intermediate tokens during execution
- **THEN** PregelRuntime SHALL yield `{"type": "message", "content": "<token>"}` for each intermediate token before yielding the superstep result

#### Scenario: MESSAGES mode falls back to VALUES for non-streaming workers
- **WHEN** PregelRuntime executes with `stream_mode=StreamMode.MESSAGES`
- **AND** a Worker does not yield intermediate tokens
- **THEN** PregelRuntime SHALL yield `{"type": "values", "state": <snapshot>}` after the superstep, same as VALUES mode
