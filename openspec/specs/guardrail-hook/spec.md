## ADDED Requirements

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
The system SHALL define a `GuardrailResult` dataclass in `engine/guardrail.py` with three fields: `action` (GuardrailAction, default ALLOW), `reason` (str, default ""), and `modified_data` (dict | None, default None).

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
The system SHALL define a `PreLLMHook` ABC in `engine/guardrail.py` with one abstract async method: `on_pre_llm_call(self, messages: list[dict], model: str, tools: list[dict] | None) -> GuardrailResult`.

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
The `engine/__init__.py` SHALL remain empty. Users import directly from submodules: `from hecate.engine.guardrail import PreLLMHook, PostLLMHook, PreToolHook, PostToolHook`.

#### Scenario: Direct submodule import
- **WHEN** code does `from hecate.engine.guardrail import PreLLMHook`
- **THEN** the import succeeds without errors
