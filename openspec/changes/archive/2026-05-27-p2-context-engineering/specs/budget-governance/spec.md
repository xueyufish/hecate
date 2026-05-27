## ADDED Requirements

### Requirement: Budget Manager tracks token usage per session
The system SHALL maintain a `BudgetManager` that tracks cumulative token usage per session and enforces a configurable token budget for the context window.

#### Scenario: Budget allocation on session start
- **WHEN** a new session is created for an agent with a configured context budget of 8000 tokens
- **THEN** the budget manager SHALL allocate 8000 tokens as the session budget and begin tracking usage

#### Scenario: Default budget when not configured
- **WHEN** an agent has no explicit context budget configured
- **THEN** the budget manager SHALL use a default budget based on the model's context window size minus 1024 tokens reserved for generation

### Requirement: Budget check before LLM invocation
The system SHALL compute the token count of the assembled context before each LLM call and trigger degradation if the count exceeds the session budget.

#### Scenario: Context within budget
- **WHEN** the assembled context token count is 6000 and the session budget is 8000
- **THEN** the budget manager SHALL allow the context to pass through unchanged

#### Scenario: Context exceeds budget triggers degradation
- **WHEN** the assembled context token count is 9000 and the session budget is 8000
- **THEN** the budget manager SHALL execute the degradation strategy to reduce the context to within budget

### Requirement: Three-level degradation strategy
The system SHALL implement three degradation levels applied sequentially until the context fits within budget:

- Level 1 (DROP): Remove messages with priority "low"
- Level 2 (COMPRESS): Compress messages with priority "medium" into a short summary
- Level 3 (EMERGENCY): Replace all message history with a single emergency summary

#### Scenario: Level 1 degradation sufficient
- **WHEN** the context exceeds budget by 1000 tokens and removing low-priority messages saves 1200 tokens
- **THEN** the budget manager SHALL remove only low-priority messages and return the trimmed context within budget

#### Scenario: Level 1 insufficient, Level 2 applied
- **WHEN** removing low-priority messages saves 500 tokens but the deficit is 2000 tokens
- **THEN** the budget manager SHALL additionally compress medium-priority messages into a summary paragraph until the context fits

#### Scenario: Level 1 and 2 insufficient, Level 3 applied
- **WHEN** both drop and compress fail to bring the context within budget
- **THEN** the budget manager SHALL replace the entire message history with a single emergency summary message containing: original objective, key decisions made, and current state

### Requirement: Budget usage reporting
The system SHALL expose budget usage metrics (allocated, used, remaining, degradation events) for observability integration.

#### Scenario: Budget snapshot after each turn
- **WHEN** an LLM call completes and returns token usage data
- **THEN** the budget manager SHALL record a snapshot containing: total budget, tokens used by the call, cumulative tokens used, remaining budget, degradation level applied (if any)
