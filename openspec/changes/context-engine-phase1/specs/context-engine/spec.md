## ADDED Requirements

### Requirement: PregelRuntime accepts optional ContextEngine parameter

PregelRuntime SHALL accept an optional `context_engine: ContextEngine | None` constructor parameter. When provided, PregelRuntime SHALL pass it to Workers via `execution_context["context_engine"]` on every superstep dispatch.

#### Scenario: PregelRuntime with ContextEngine

- **WHEN** PregelRuntime is constructed with a ContextEngine instance
- **THEN** the execution_context dict passed to Workers SHALL contain key `"context_engine"` with the ContextEngine instance as its value

#### Scenario: PregelRuntime without ContextEngine (backward compatible)

- **WHEN** PregelRuntime is constructed without a context_engine parameter (or with None)
- **THEN** the execution_context dict SHALL NOT contain key `"context_engine"`
- **AND** all existing behavior SHALL remain unchanged

### Requirement: LLMWorker applies context pipeline before LLM invocation

LLMWorker SHALL check execution_context for a ContextEngine instance. When present, LLMWorker SHALL apply a 4-step context pipeline to the messages extracted from channel_snapshot before passing them to `port.llm_invoke()`:

1. Tool result truncation: cap each tool result content to `tool_result_limit` tokens (default 2000)
2. Token estimation: call `context_engine.estimate_tokens(messages)` 
3. Message selection: if estimated tokens exceed budget, call `context_engine.select_messages(messages, budget)`
4. Compression: if selected messages still exceed budget, call `context_engine.compress(selected)`

The pipeline SHALL be applied in both `execute()` and `execute_stream()` methods.

#### Scenario: Context pipeline applied when ContextEngine is present

- **WHEN** LLMWorker receives an execution_context containing `"context_engine"`
- **AND** the messages list exceeds the token budget
- **THEN** LLMWorker SHALL apply message selection to fit within the budget
- **AND** the filtered messages SHALL be passed to `port.llm_invoke()` instead of the full list

#### Scenario: Context pipeline skipped when ContextEngine is absent

- **WHEN** LLMWorker receives an execution_context without `"context_engine"`
- **THEN** LLMWorker SHALL pass the full messages list to `port.context_assemble()` and `port.llm_invoke()` as before
- **AND** no filtering, selection, or compression SHALL occur

#### Scenario: Both execute and execute_stream apply pipeline

- **WHEN** ContextEngine is present and messages exceed budget
- **THEN** both `execute()` and `execute_stream()` SHALL apply the same context pipeline
- **AND** streaming tokens SHALL correspond to the filtered messages, not the full history

### Requirement: Context pipeline is non-destructive

The context pipeline SHALL NOT modify the channel snapshot, channel state, or checkpoint data. The filtered messages SHALL be a temporary copy used only for the current LLM invocation. The original `messages` list in the channel SHALL retain all messages.

#### Scenario: Channel messages unchanged after LLM call

- **WHEN** LLMWorker applies the context pipeline, filtering messages from 100 to 20
- **AND** the WorkerResult is applied to channels via `_apply_writes`
- **THEN** the channel `messages` field SHALL contain the original 100 messages plus the new assistant message
- **AND** no messages SHALL have been removed by the context pipeline

#### Scenario: Checkpoint retains full message history

- **WHEN** PregelRuntime saves a checkpoint after a superstep where context pipeline was applied
- **THEN** the checkpoint SHALL contain the complete, unfiltered message history
- **AND** restoring from this checkpoint SHALL provide access to all messages

### Requirement: Token budget resolution priority

The token budget for message selection SHALL be resolved in the following priority order:

1. `node_config.get("max_tokens")` — per-node explicit configuration
2. `execution_context.get("context_budget")` — runtime-wide global budget
3. `8000` — default budget

#### Scenario: Per-node budget takes priority

- **WHEN** node_config contains `"max_tokens": 16000`
- **AND** execution_context contains `"context_budget": 8000`
- **THEN** the budget used for message selection SHALL be 16000

#### Scenario: Runtime budget used when no per-node config

- **WHEN** node_config does not contain `"max_tokens"`
- **AND** execution_context contains `"context_budget": 12000`
- **THEN** the budget used for message selection SHALL be 12000

#### Scenario: Default budget when no config

- **WHEN** node_config does not contain `"max_tokens"`
- **AND** execution_context does not contain `"context_budget"`
- **THEN** the budget used for message selection SHALL be 8000

### Requirement: Tool result truncation before message selection

Before message selection, LLMWorker SHALL truncate individual tool result messages whose content exceeds `tool_result_limit` tokens (default 2000, configurable via `node_config.get("tool_result_limit")`). Truncation SHALL preserve the first N tokens of the tool result content and append a truncation indicator.

#### Scenario: Oversized tool result truncated

- **WHEN** a tool result message contains 5000 tokens of content
- **AND** tool_result_limit is 2000
- **THEN** the tool result content SHALL be truncated to approximately 2000 tokens
- **AND** a truncation indicator SHALL be appended to signal that content was removed

#### Scenario: Small tool result preserved

- **WHEN** a tool result message contains 500 tokens of content
- **AND** tool_result_limit is 2000
- **THEN** the tool result content SHALL remain unchanged
