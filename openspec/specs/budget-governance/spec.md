## Purpose

Defines session budget governance: per-session token tracking, pre-invocation budget checks, a three-level degradation strategy, and budget usage reporting.

## Requirements

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

The system SHALL implement degradation levels applied sequentially until the context fits within budget, in lossiness order — least destructive first:

- Level 0 (WARN): when estimated usage crosses the warn threshold, inject a model-visible budget hint so the agent can converge on its own (persistent hint message; system prompt untouched; at most once per crossing)
- Level 1 (DROP): select messages/units to fit the budget (window selection; importance ranking inside the droppable region), with offload preservation of the dropped block when an offloader is available
- Level 2 (COMPRESS): compress the remaining context (transient projection by default; durable surface-replacement backend when configured)
- Level 3 (TERMINATE): controlled termination — strip pending tool calls from the trailing assistant message so the agent loop finalizes, preserve system messages and the newest user message, complete the invocation, and surface `stop_reason="token_capped"`

Silent content truncation SHALL NOT be used as the final degradation level.

#### Scenario: Warn hint fires before destructive levels

- **WHEN** estimated usage crosses the warn threshold but is still within budget
- **THEN** only the warn hint SHALL be applied and the context SHALL pass through unmodified

#### Scenario: Level 1 degradation sufficient

- **WHEN** the context exceeds budget by 1000 tokens and dropping the oldest units saves 1200 tokens
- **THEN** the system SHALL apply only the warn (if threshold crossed) and DROP levels and return the trimmed context within budget

#### Scenario: Level 1 insufficient, Level 2 applied

- **WHEN** dropping saves 500 tokens but the deficit is 2000 tokens
- **THEN** the system SHALL additionally compress the remaining context until it fits

#### Scenario: Levels 0-2 insufficient, Level 3 terminates the turn in a controlled manner

- **WHEN** warn, drop, offload, and compress all fail to bring the context within budget
- **THEN** the system SHALL strip pending tool calls from the trailing assistant message
- **AND** the invocation SHALL complete with `stop_reason="token_capped"`
- **AND** the full degradation trail SHALL be visible in the budget snapshot

### Requirement: Budget usage reporting

The system SHALL expose budget usage metrics (allocated, used, remaining, degradation events) for observability integration.

#### Scenario: Budget snapshot after each turn

- **WHEN** an LLM call completes and returns token usage data
- **THEN** the budget manager SHALL record a snapshot containing: total budget, tokens used by the call, cumulative tokens used, remaining budget, degradation levels applied in execution order (subset of `warn`, `drop`, `compress`, `terminate`), `stop_reason` (present when the terminate level fired), and — when the provider reports them — cached vs total input tokens for cache hit-rate computation
