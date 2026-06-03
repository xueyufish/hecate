## ADDED Requirements

### Requirement: GuardrailAction enum
The system SHALL define `GuardrailAction` as a `StrEnum` with two members: `ALLOW` and `BLOCK`.

#### Scenario: String comparison
- **WHEN** `result.action == GuardrailAction.ALLOW`
- **THEN** the comparison evaluates to `True`

#### Scenario: Literal string comparison
- **WHEN** `result.action == "allow"`
- **THEN** the comparison evaluates to `True` (StrEnum compatibility)

#### Scenario: Only two members
- **WHEN** `len(GuardrailAction)` is evaluated
- **THEN** the result is `2`

### Requirement: GuardrailResult dataclass
The system SHALL define a `GuardrailResult` dataclass in `engine/guardrail.py` with two fields: `action` (GuardrailAction, default ALLOW) and `reason` (str, default "").

#### Scenario: Allow action with defaults
- **WHEN** `GuardrailResult()` is constructed
- **THEN** `action` is `GuardrailAction.ALLOW` and `reason` is `""`

#### Scenario: Block action with reason
- **WHEN** `GuardrailResult(action=GuardrailAction.BLOCK, reason="Prompt injection")` is constructed
- **THEN** `action` is `GuardrailAction.BLOCK` and `reason` is `"Prompt injection"`

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
The system SHALL provide four NoOp classes (`NoOpPreLLMHook`, `NoOpPostLLMHook`, `NoOpPreToolHook`, `NoOpPostToolHook`), each inheriting from its respective ABC and returning `GuardrailResult(action=GuardrailAction.ALLOW)` from its method.

#### Scenario: NoOpPreLLMHook returns allow
- **WHEN** `NoOpPreLLMHook().on_pre_llm_call(messages, model, tools)` is called
- **THEN** it returns `GuardrailResult(action=GuardrailAction.ALLOW)`

#### Scenario: NoOpPostLLMHook returns allow
- **WHEN** `NoOpPostLLMHook().on_post_llm_call(response, messages)` is called
- **THEN** it returns `GuardrailResult(action=GuardrailAction.ALLOW)`

#### Scenario: NoOpPreToolHook returns allow
- **WHEN** `NoOpPreToolHook().on_pre_tool_call(name, arguments, context)` is called
- **THEN** it returns `GuardrailResult(action=GuardrailAction.ALLOW)`

#### Scenario: NoOpPostToolHook returns allow
- **WHEN** `NoOpPostToolHook().on_post_tool_call(name, result, context)` is called
- **THEN** it returns `GuardrailResult(action=GuardrailAction.ALLOW)`

### Requirement: Module structure
The `engine/__init__.py` SHALL remain empty. Users import directly from submodules: `from hecate.engine.guardrail import PreLLMHook, PostLLMHook, PreToolHook, PostToolHook`.

#### Scenario: Direct submodule import
- **WHEN** code does `from hecate.engine.guardrail import PreLLMHook`
- **THEN** the import succeeds without errors
