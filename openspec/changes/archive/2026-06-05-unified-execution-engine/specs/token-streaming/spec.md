## ADDED Requirements

### Requirement: StreamMode.MESSAGES yields individual tokens
When `StreamMode.MESSAGES` is active, PregelRuntime SHALL yield individual token events from Workers during the dispatch phase, before yielding the standard superstep events.

#### Scenario: Token streaming from LLM Worker
- **WHEN** a CONVERSATION node is dispatched with StreamMode.MESSAGES
- **AND** the Worker yields tokens during execution
- **THEN** PregelRuntime SHALL yield `{"type": "message", "content": "<token>"}` for each token, then yield `{"type": "values", "state": <snapshot>}` after the superstep completes

#### Scenario: Multiple tokens from single node
- **WHEN** a Worker yields 50 tokens followed by a final WorkerResult
- **THEN** PregelRuntime SHALL yield 50 `{"type": "message"}` events followed by one `{"type": "values"}` event

#### Scenario: Non-streaming Workers
- **WHEN** a Worker (e.g., ConditionWorker) does not yield tokens
- **THEN** PregelRuntime SHALL only yield the standard `{"type": "values"}` event after the superstep

### Requirement: Worker streaming interface
Workers SHALL support an optional streaming interface where they yield partial results before returning the final WorkerResult.

#### Scenario: Worker yields tokens
- **WHEN** a Worker's execute method is an AsyncGenerator instead of a coroutine
- **THEN** PregelRuntime SHALL consume the generator, yielding each intermediate item as a MESSAGES event, and using the final yielded item as the WorkerResult
