## ADDED Requirements

### Requirement: Context Assembler assembles context before LLM invocation
The system SHALL provide a `ContextAssembler` that accepts raw messages, tools, knowledge chunks, and session metadata, and returns an `AssembledContext` containing the final messages list, tool definitions, and metadata to pass to the LLM service.

#### Scenario: Simple pass-through when context engineering is disabled
- **WHEN** context engineering is not enabled for the agent
- **THEN** the assembler SHALL return the original messages and tools unchanged

#### Scenario: Assembly with task phase detection
- **WHEN** context engineering is enabled and messages contain a conversation history of 10+ turns
- **THEN** the assembler SHALL detect the current task phase (explore, converge, execute, verify) based on the recent message pattern and annotate the assembled context with the detected phase

### Requirement: Dynamic capability view filters available tools per turn
The system SHALL filter the tool list presented to the LLM based on the current task phase and agent configuration, rather than always presenting all tools.

#### Scenario: Explore phase tool filtering
- **WHEN** the detected task phase is "explore" and the agent has 20 registered tools
- **THEN** the assembler SHALL include only tools tagged as relevant for exploration (search, read, list) and exclude execution tools (write, delete, deploy)

#### Scenario: All tools available in execute phase
- **WHEN** the detected task phase is "execute"
- **THEN** the assembler SHALL include all tools available to the agent without filtering

#### Scenario: No phase filtering when phases not configured
- **WHEN** the agent has no phase-to-tool mapping configured
- **THEN** the assembler SHALL pass all tools through without filtering

### Requirement: Task work panel structures context for the current task
The system SHALL construct a structured task work panel that replaces raw message history with a focused context window containing: current objective, relevant prior decisions, active evidence, and pending actions.

#### Scenario: Work panel for multi-turn task
- **WHEN** a conversation has 15 turns and the current task involves tool calling
- **THEN** the assembler SHALL construct a work panel containing: (1) the original objective from the first user message, (2) the last 3 assistant-user exchanges, (3) the most recent tool call and result, (4) a summary of older messages

#### Scenario: Work panel falls back to full history for short conversations
- **WHEN** a conversation has 3 or fewer turns
- **THEN** the assembler SHALL pass all messages through without constructing a work panel

### Requirement: Context priority assignment
The system SHALL assign a priority level (critical, high, medium, low) to each message in the conversation history for use by the budget governance module.

#### Scenario: Priority assignment rules
- **WHEN** messages are processed by the assembler
- **THEN** system messages and the current user message SHALL be marked "critical"; the last 3 assistant-user exchanges SHALL be marked "high"; tool results within the last 2 turns SHALL be marked "high"; older messages SHALL be marked "medium" or "low" based on recency

#### Scenario: Priority preserved in assembled output
- **WHEN** the assembler returns an AssembledContext
- **THEN** each message SHALL include a `priority` field accessible to downstream consumers (budget manager, evidence tracker)
