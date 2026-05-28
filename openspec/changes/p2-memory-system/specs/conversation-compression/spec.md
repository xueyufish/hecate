## ADDED Requirements

### Requirement: Snip low-value messages
The system SHALL remove messages with low information density when the context exceeds the token budget.

#### Scenario: Remove system notifications
- **WHEN** the context exceeds budget and contains system notification messages
- **THEN** the system SHALL remove those messages first

#### Scenario: Preserve recent messages
- **WHEN** applying snip
- **THEN** the system SHALL preserve the most recent N messages (configurable, default 6)

### Requirement: Microcompact consecutive messages
The system SHALL merge consecutive messages from the same role into a single message.

#### Scenario: Merge consecutive user messages
- **WHEN** there are 3 consecutive user messages
- **THEN** the system SHALL merge them into a single user message with combined content

#### Scenario: Preserve message boundaries
- **WHEN** merging messages
- **THEN** the system SHALL add separators between merged content

### Requirement: Autocompact with LLM summary
The system SHALL use the LLM to generate a summary of older messages when snip and microcompact are insufficient.

#### Scenario: Generate summary
- **WHEN** snip and microcompact cannot reduce context below budget
- **THEN** the system SHALL call the LLM to summarize messages older than the recent window

#### Scenario: Summary replaces old messages
- **WHEN** a summary is generated
- **THEN** the system SHALL replace the summarized messages with a single summary message

### Requirement: Compression pipeline integration
The system SHALL integrate the compression pipeline into the context assembly process.

#### Scenario: Compression applied before LLM call
- **WHEN** context is assembled and exceeds budget
- **THEN** the system SHALL apply snip → microcompact → autocompact in order until within budget

#### Scenario: Compression skips recent messages
- **WHEN** applying compression
- **THEN** the system SHALL not compress messages within the recent window

### Requirement: Compression metadata
The system SHALL track compression statistics for observability.

#### Scenario: Track compression events
- **WHEN** compression is applied
- **THEN** the system SHALL record: original token count, compressed token count, compression level applied, messages affected
