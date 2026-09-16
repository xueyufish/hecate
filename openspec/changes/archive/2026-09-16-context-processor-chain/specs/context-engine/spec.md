## MODIFIED Requirements

### Requirement: LLMWorker applies context pipeline before LLM invocation

LLMWorker SHALL check execution_context for a configured context processor chain. When present, LLMWorker SHALL delegate all context projection to the chain before passing messages extracted from channel_snapshot to `port.llm_invoke()`. The default chain SHALL reproduce the legacy five-step pipeline behavior — tool result truncation (cap each tool result content to `tool_result_limit` tokens, default 2000) → token estimation → message selection (DROP) when over budget → context offloading of the dropped block when a `ContextOffloader` is available in `execution_context["context_offloader"]` and the dropped tokens meet the offload threshold → compression as last resort — and SHALL additionally apply the budget warn hint level before DROP and controlled termination when still over budget after compression. When neither a chain nor a legacy `ContextEngine` is present, LLMWorker SHALL pass the full messages list to `port.context_assemble()` and `port.llm_invoke()` as before; when only a legacy `ContextEngine` is present (no chain configured), LLMWorker SHALL run the default chain, which consumes the engine for selection and compression. The pipeline SHALL be applied in both `execute()` and `execute_stream()` methods.

#### Scenario: Context pipeline applied when chain is present

- **WHEN** LLMWorker receives an execution_context containing a configured context processor chain
- **AND** the messages list exceeds the token budget
- **THEN** the chain SHALL apply its processors so the projected messages fit within the budget
- **AND** the projected messages SHALL be passed to `port.llm_invoke()` instead of the full list

#### Scenario: Legacy engine without a chain runs the default chain

- **WHEN** execution_context contains a `ContextEngine` but no configured chain
- **THEN** LLMWorker SHALL apply the default chain, which delegates selection and compression to the engine

#### Scenario: Context pipeline skipped when chain is absent

- **WHEN** LLMWorker receives an execution_context without a configured context processor chain
- **THEN** LLMWorker SHALL pass the full messages list to `port.context_assemble()` and `port.llm_invoke()` as before
- **AND** no filtering, selection, offloading, or compression SHALL occur

#### Scenario: Both execute and execute_stream apply pipeline

- **WHEN** a chain is present and messages exceed budget
- **THEN** both `execute()` and `execute_stream()` SHALL apply the same chain projection
- **AND** streaming tokens SHALL correspond to the projected messages, not the full history

#### Scenario: Offload step invoked when offloader is available

- **WHEN** execution_context contains `"context_offloader"` with a valid ContextOffloader
- **AND** message selection drops messages totaling at least the offload threshold tokens
- **THEN** the dropped messages SHALL be offloaded to the environment as a JSON file
- **AND** a compact reference stub SHALL replace the dropped block in the live context
- **AND** the chain SHALL recompute token count on `[stub + selected]` before deciding whether to compress

#### Scenario: Offload skipped when offloader is absent

- **WHEN** execution_context does NOT contain `"context_offloader"`
- **AND** messages exceed budget after selection
- **THEN** the chain SHALL proceed directly to compression
- **AND** no file writes SHALL occur

#### Scenario: Compression fires when offload insufficient

- **WHEN** offload has occurred (stub + selected) but token count still exceeds budget
- **THEN** the chain SHALL apply compression on the `[stub + selected]` list
- **AND** if the context still exceeds the budget after compression, the chain SHALL degrade the turn via controlled termination (strip pending tool calls, complete the invocation, surface `stop_reason="token_capped"`)

### Requirement: Token budget resolution priority

The token budget for message selection SHALL be resolved in the following priority order:

1. `node_config.get("max_tokens")` — per-node explicit configuration
2. `execution_context.get("context_budget")` — runtime-wide global budget
3. model-capability default — derived from the resolved model's context window policy when known
4. `8000` — default budget

#### Scenario: Per-node budget takes priority

- **WHEN** node_config contains `"max_tokens": 16000`
- **AND** execution_context contains `"context_budget": 8000`
- **THEN** the budget used for message selection SHALL be 16000

#### Scenario: Runtime budget used when no per-node config

- **WHEN** node_config does not contain `"max_tokens"`
- **AND** execution_context contains `"context_budget": 12000`
- **THEN** the budget used for message selection SHALL be 12000

#### Scenario: Model-capability default used when no explicit config

- **WHEN** node_config does not contain `"max_tokens"`
- **AND** execution_context does not contain `"context_budget"`
- **AND** the resolved model has a known context window policy
- **THEN** the budget SHALL be derived from the model-capability default

#### Scenario: Default budget when nothing else applies

- **WHEN** no per-node config, runtime budget, or model-capability default is available
- **THEN** the budget used for message selection SHALL be 8000
