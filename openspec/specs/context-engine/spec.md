## ADDED Requirements

### Requirement: ContextEngine ABC defines pluggable context management
The engine SHALL define a `ContextEngine` ABC in `engine/context.py` with methods: `select_messages`, `compress`, `estimate_tokens`.

#### Scenario: Select messages within budget
- **WHEN** `select_messages(history, budget)` is called with message history and token budget
- **THEN** it SHALL return a list of messages that fit within the budget

#### Scenario: Compress messages
- **WHEN** `compress(messages)` is called with a list of messages
- **THEN** it SHALL return a compressed version of the messages (fewer tokens)

#### Scenario: Estimate token count
- **WHEN** `estimate_tokens(messages)` is called with a list of messages
- **THEN** it SHALL return an integer estimate of the total token count

### Requirement: InMemoryContextEngine provides default implementation
An `InMemoryContextEngine` SHALL implement ContextEngine using simple heuristics suitable for testing and single-machine deployment.

#### Scenario: Select recent messages within budget
- **WHEN** `select_messages(history, budget)` is called with 10 messages and budget allows 5
- **THEN** it SHALL return the 5 most recent messages

#### Scenario: Compress by truncating oldest
- **WHEN** `compress(messages)` is called with messages exceeding threshold
- **THEN** it SHALL return messages with oldest ones removed or summarized

#### Scenario: Simple token estimation
- **WHEN** `estimate_tokens(messages)` is called
- **THEN** it SHALL return an estimate based on character count (approximately 4 chars per token)

#### Scenario: Empty message list
- **WHEN** `select_messages([], 1000)` is called
- **THEN** it SHALL return `[]`

#### Scenario: Zero budget
- **WHEN** `select_messages(history, 0)` is called
- **THEN** it SHALL return `[]`
