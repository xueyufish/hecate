## MODIFIED Requirements

### Requirement: LLMWorker applies context pipeline before LLM invocation

LLMWorker SHALL check execution_context for a ContextEngine instance. When present, LLMWorker SHALL apply a 5-step context pipeline to the messages extracted from channel_snapshot before passing them to `port.llm_invoke()`:

1. Tool result truncation: cap each tool result content to `tool_result_limit` tokens (default 2000)
2. Token estimation: call `context_engine.estimate_tokens(messages)`
3. Message selection: if estimated tokens exceed budget, call `context_engine.select_messages(messages, budget)`
4. Context offloading: if a `ContextOffloader` is available in `execution_context["context_offloader"]` and the dropped messages meet the offload threshold, offload the dropped messages to the environment and replace them with a compact reference stub
5. Compression: if the `[stub + selected]` messages still exceed budget, call `context_engine.compress(selected)` as a last resort

The pipeline SHALL be applied in both `execute()` and `execute_stream()` methods.

#### Scenario: Context pipeline applied when ContextEngine is present

- **WHEN** LLMWorker receives an execution_context containing `"context_engine"`
- **AND** the messages list exceeds the token budget
- **THEN** LLMWorker SHALL apply message selection to fit within the budget
- **AND** the filtered messages SHALL be passed to `port.llm_invoke()` instead of the full list

#### Scenario: Context pipeline skipped when ContextEngine is absent

- **WHEN** LLMWorker receives an execution_context without `"context_engine"`
- **THEN** LLMWorker SHALL pass the full messages list to `port.context_assemble()` and `port.llm_invoke()` as before
- **AND** no filtering, selection, offloading, or compression SHALL occur

#### Scenario: Both execute and execute_stream apply pipeline

- **WHEN** ContextEngine is present and messages exceed budget
- **THEN** both `execute()` and `execute_stream()` SHALL apply the same context pipeline
- **AND** streaming tokens SHALL correspond to the filtered messages, not the full history

#### Scenario: Offload step invoked when offloader is available

- **WHEN** execution_context contains `"context_offloader"` with a valid ContextOffloader
- **AND** message selection drops messages totaling at least `CONTEXT_OFFLOAD_THRESHOLD_TOKENS`
- **THEN** the dropped messages SHALL be offloaded to the environment as a JSON file
- **AND** a compact reference stub SHALL replace the dropped block in the live context
- **AND** the pipeline SHALL recompute token count on `[stub + selected]` before deciding whether to compress

#### Scenario: Offload skipped when offloader is absent

- **WHEN** execution_context does NOT contain `"context_offloader"`
- **AND** messages exceed budget after selection
- **THEN** the pipeline SHALL proceed directly to compression (step 5)
- **AND** no file writes SHALL occur
- **AND** behavior SHALL match the pre-offload 4-step pipeline exactly

#### Scenario: Compression fires when offload insufficient

- **WHEN** offload has occurred (stub + selected) but token count still exceeds budget
- **THEN** the pipeline SHALL call `context_engine.compress()` on the `[stub + selected]` list
- **AND** compression SHALL drop oldest items from the `[stub + selected]` list as the last resort

### Requirement: Context pipeline is non-destructive

The context pipeline SHALL NOT modify the channel snapshot, channel state, or checkpoint data. The filtered messages SHALL be a temporary copy used only for the current LLM invocation. The original `messages` list in the channel SHALL retain all messages. Offloaded files SHALL be additional artifacts stored in the environment — they do NOT replace or remove the channel's message history.

#### Scenario: Channel messages unchanged after LLM call

- **WHEN** LLMWorker applies the context pipeline, filtering messages from 100 to 20
- **AND** the WorkerResult is applied to channels via `_apply_writes`
- **THEN** the channel `messages` field SHALL contain the original 100 messages plus the new assistant message
- **AND** no messages SHALL have been removed by the context pipeline

#### Scenario: Checkpoint retains full message history

- **WHEN** PregelRuntime saves a checkpoint after a superstep where context pipeline was applied
- **THEN** the checkpoint SHALL contain the complete, unfiltered message history
- **AND** restoring from this checkpoint SHALL provide access to all messages

#### Scenario: Offload does not mutate channel messages

- **WHEN** the offload step writes dropped messages to the environment
- **THEN** the channel's `messages` list SHALL remain unchanged
- **AND** the offloaded file SHALL be a separate copy stored in the environment filesystem
