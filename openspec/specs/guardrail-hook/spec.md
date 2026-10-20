## Purpose

Defines guardrail hooks: the GuardrailAction and GuardrailResult contract, pre/post LLM and pre/post tool hook interfaces, no-op pass-through defaults, and SANITIZE handling in workers.

## Requirements

### Requirement: GuardrailAction enum
The system SHALL define `GuardrailAction` as a `StrEnum` with three members: `ALLOW`, `BLOCK`, and `SANITIZE`.

#### Scenario: String comparison
- **WHEN** `result.action == GuardrailAction.ALLOW`
- **THEN** the comparison evaluates to `True`

#### Scenario: Literal string comparison
- **WHEN** `result.action == "allow"`
- **THEN** the comparison evaluates to `True` (StrEnum compatibility)

#### Scenario: Sanitize action
- **WHEN** `result.action == GuardrailAction.SANITIZE`
- **THEN** the comparison evaluates to `True`

#### Scenario: Three members
- **WHEN** `len(GuardrailAction)` is evaluated
- **THEN** the result is `3`

#### Scenario: Sanitize string value
- **WHEN** `GuardrailAction.SANITIZE` is converted to string
- **THEN** the value is `"sanitize"`

### Requirement: GuardrailResult dataclass
The system SHALL define a `GuardrailResult` dataclass in `runtime/guardrail.py` with three fields: `action` (GuardrailAction, default ALLOW), `reason` (str, default ""), and `modified_data` (dict | None, default None).

#### Scenario: Allow action with defaults
- **WHEN** `GuardrailResult()` is constructed
- **THEN** `action` is `GuardrailAction.ALLOW`, `reason` is `""`, and `modified_data` is `None`

#### Scenario: Block action with reason
- **WHEN** `GuardrailResult(action=GuardrailAction.BLOCK, reason="Prompt injection")` is constructed
- **THEN** `action` is `GuardrailAction.BLOCK`, `reason` is `"Prompt injection"`, and `modified_data` is `None`

#### Scenario: Sanitize action with modified data
- **WHEN** `GuardrailResult(action=GuardrailAction.SANITIZE, modified_data={"messages": [...]})` is constructed
- **THEN** `action` is `GuardrailAction.SANITIZE` and `modified_data` is `{"messages": [...]}`

### Requirement: PreLLMHook abstract base class
The system SHALL define a `PreLLMHook` ABC in `runtime/guardrail.py` with one abstract async method: `on_pre_llm_call(self, messages: list[dict], model: str, tools: list[dict] | None) -> GuardrailResult`.

#### Scenario: Cannot instantiate directly
- **WHEN** `PreLLMHook()` is called
- **THEN** `TypeError` is raised

#### Scenario: Subclass with implementation succeeds
- **WHEN** a class inherits from `PreLLMHook` and implements `on_pre_llm_call`
- **THEN** the class can be instantiated

### Requirement: PostLLMHook abstract base class
The system SHALL define a `PostLLMHook` ABC with one abstract async method: `on_post_llm_call(self, response: dict, messages: list[dict]) -> GuardrailResult`.

#### Scenario: Cannot instantiate directly
- **WHEN** `PostLLMHook()` is called
- **THEN** `TypeError` is raised

### Requirement: PreToolHook abstract base class
The system SHALL define a `PreToolHook` ABC with one abstract async method: `on_pre_tool_call(self, name: str, arguments: dict, context: dict | None) -> GuardrailResult`.

#### Scenario: Cannot instantiate directly
- **WHEN** `PreToolHook()` is called
- **THEN** `TypeError` is raised

### Requirement: PostToolHook abstract base class
The system SHALL define a `PostToolHook` ABC with one abstract async method: `on_post_tool_call(self, name: str, result: Any, context: dict | None) -> GuardrailResult`.

#### Scenario: Cannot instantiate directly
- **WHEN** `PostToolHook()` is called
- **THEN** `TypeError` is raised

### Requirement: NoOp pass-through implementations
The system SHALL provide four NoOp classes (`NoOpPreLLMHook`, `NoOpPostLLMHook`, `NoOpPreToolHook`, `NoOpPostToolHook`), each inheriting from its respective ABC and returning `GuardrailResult(action=GuardrailAction.ALLOW, modified_data=None)` from its method.

#### Scenario: NoOpPreLLMHook returns allow without modified_data
- **WHEN** `NoOpPreLLMHook().on_pre_llm_call(messages, model, tools)` is called
- **THEN** it returns `GuardrailResult(action=GuardrailAction.ALLOW, modified_data=None)`

#### Scenario: NoOpPostLLMHook returns allow without modified_data
- **WHEN** `NoOpPostLLMHook().on_post_llm_call(response, messages)` is called
- **THEN** it returns `GuardrailResult(action=GuardrailAction.ALLOW, modified_data=None)`

#### Scenario: NoOpPreToolHook returns allow without modified_data
- **WHEN** `NoOpPreToolHook().on_pre_tool_call(name, arguments, context)` is called
- **THEN** it returns `GuardrailResult(action=GuardrailAction.ALLOW, modified_data=None)`

#### Scenario: NoOpPostToolHook returns allow without modified_data
- **WHEN** `NoOpPostToolHook().on_post_tool_call(name, result, context)` is called
- **THEN** it returns `GuardrailResult(action=GuardrailAction.ALLOW, modified_data=None)`

### Requirement: Workers handle SANITIZE action
LLMWorker and ToolWorker SHALL handle the SANITIZE action by replacing the relevant data with `modified_data` content.

#### Scenario: LLMWorker receives SANITIZE from PreLLMHook
- **WHEN** `PreLLMHook` returns `GuardrailResult(action=SANITIZE, modified_data={"messages": <anonymized>})`
- **THEN** LLMWorker SHALL use the anonymized messages for the LLM call instead of the original

#### Scenario: LLMWorker receives SANITIZE from PostLLMHook
- **WHEN** `PostLLMHook` returns `GuardrailResult(action=SANITIZE, modified_data={"response": <sanitized>})`
- **THEN** LLMWorker SHALL use the sanitized response in channel updates

#### Scenario: ToolWorker receives SANITIZE from PostToolHook
- **WHEN** `PostToolHook` returns `GuardrailResult(action=SANITIZE, modified_data={"result": <masked>})`
- **THEN** ToolWorker SHALL use the masked result in the tool result message

#### Scenario: SANITIZE with empty modified_data
- **WHEN** `GuardrailResult(action=SANITIZE, modified_data=None)` is returned
- **THEN** the worker SHALL treat it as ALLOW (pass through unchanged) and log a warning

### Requirement: Module structure
The `runtime/__init__.py` SHALL remain empty. Users import directly from submodules: `from hecate.runtime.guardrail import PreLLMHook, PostLLMHook, PreToolHook, PostToolHook`.

#### Scenario: Direct submodule import
- **WHEN** code does `from hecate.runtime.guardrail import PreLLMHook`
- **THEN** the import succeeds without errors

### Requirement: Successful replay reuses PostTool output governance

The shared tool call executor SHALL re-apply the same PostTool output governance it applies on first execution — the `TOOL_RESULT` middleware chain taking precedence over the legacy `PostToolHook` — to ledger-backed content at every successful replay exit (recovery pre-check, authoritative in-lock re-check, and ledger claim-race backfill) before that content is returned as a tool message. A `BLOCK` SHALL yield the same governed placeholder shape as first execution; a `SANITIZE` SHALL apply `modified_data["result"]`; an `ALLOW` SHALL pass the content through unchanged. The ledger-recorded content is fed to governance as recorded; governance SHALL NOT assume it is the original structured result object. A governance failure on the replay path SHALL NOT return the original content and SHALL fail closed under the existing unknown/reconciliation semantics. Replay governance is local output processing only: it SHALL NOT re-execute the business tool, re-consume an approval, or re-claim a settled action, and SHALL NOT rewrite a ledger-recorded business success into a failure. First-execution governance and the durable outcome-mirroring order (ledger write before PostTool) SHALL remain unchanged, as SHALL the channel-history backfill path whose content is already the first-run governed result.

#### Scenario: Replay applies chain precedence

- **WHEN** a successful replay resolves content from the ledger and a `TOOL_RESULT` middleware chain is configured
- **THEN** the executor applies the chain — not the legacy hook — to that content before returning it, mirroring first-execution precedence

#### Scenario: BLOCK on replay hides first-run content

- **WHEN** first execution stored the raw result in the ledger and PostTool governance blocks the same content on replay
- **THEN** the replayed tool message carries the governed placeholder, the business tool is not re-executed, and the ledger outcome remains `succeeded`

#### Scenario: SANITIZE on replay masks content

- **WHEN** the governance pipeline returns `SANITIZE` with `modified_data["result"]` for replayed content
- **THEN** the returned tool message carries the masked content

#### Scenario: Governance error fails closed

- **WHEN** output governance raises an exception on a replay path
- **THEN** the executor stops under the existing reconciliation semantics and the original content never reaches the model or channel

#### Scenario: Channel-history backfill stays governed-once

- **WHEN** the channel history already holds the first-run tool message for the call
- **THEN** the executor returns it as-is without applying governance a second time

#### Scenario: No hook or chain configured

- **WHEN** no `TOOL_RESULT` middleware chain and no matching legacy `PostToolHook` is configured
- **THEN** replayed ledger content passes through unchanged, identical to first-execution behavior

#### Scenario: Empty recorded content still passes through governance

- **WHEN** the ledger records a successful result whose content is an empty string
- **THEN** replay SHALL apply the configured output governance; an empty string SHALL NOT be treated as missing content

#### Scenario: Claim-race backfill preserves an already governed channel result

- **WHEN** a competing ledger claim resolves as succeeded and recovery selects an existing channel tool message
- **THEN** the executor SHALL return that governed message without treating it as ledger raw content or applying PostTool twice

#### Scenario: Replay governance receives trusted execution correlation

- **WHEN** a ledger result is passed to output governance during recovery pre-check or claim-race backfill
- **THEN** the context SHALL carry the actual tool_call_id and server-derived stable invocation_id, overriding caller-supplied values without mutating the caller context
