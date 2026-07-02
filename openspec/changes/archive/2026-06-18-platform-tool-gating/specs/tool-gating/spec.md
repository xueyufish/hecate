## ADDED Requirements

### Requirement: ToolModel supports available_when field

`ToolModel` SHALL accept an optional `available_when: str | None` field. When `None` (default), the tool SHALL be always available. When a non-empty string, the string SHALL be a Python expression evaluated against runtime context to determine tool visibility.

#### Scenario: Tool without available_when is always visible

- **WHEN** a tool has `available_when=None`
- **THEN** the tool SHALL appear in the LLM's tool list regardless of runtime context

#### Scenario: Tool with available_when expression

- **WHEN** a tool has `available_when="user_role == 'admin'"`
- **AND** the runtime context has `user_role='admin'`
- **THEN** the tool SHALL appear in the LLM's tool list

#### Scenario: Tool with available_when expression evaluates to False

- **WHEN** a tool has `available_when="user_role == 'admin'"`
- **AND** the runtime context has `user_role='guest'`
- **THEN** the tool SHALL NOT appear in the LLM's tool list

### Requirement: ToolGateEvaluator evaluates expressions in restricted namespace

The system SHALL provide a `ToolGateEvaluator` class in `engine/tool_gate.py` that evaluates `available_when` expressions using Python's `eval()` with a restricted namespace (`__builtins__: {}`, no `__import__`, no built-in functions). The evaluator SHALL only have access to variables explicitly provided in the runtime context dict.

#### Scenario: Simple equality expression

- **WHEN** `evaluator.evaluate("user_role == 'admin'", {"user_role": "admin"})` is called
- **THEN** it SHALL return `True`

#### Scenario: Compound expression with and/or

- **WHEN** `evaluator.evaluate("phase == 'EXECUTE' and budget > 1000", {"phase": "EXECUTE", "budget": 5000})` is called
- **THEN** it SHALL return `True`

#### Scenario: Membership check expression

- **WHEN** `evaluator.evaluate("'delete' in permissions", {"permissions": ["read", "write", "delete"]})` is called
- **THEN** it SHALL return `True`

#### Scenario: Expression cannot access builtins

- **WHEN** `evaluator.evaluate("__import__('os').system('rm -rf /')", {})` is called
- **THEN** the expression SHALL raise `NameError` and the evaluator SHALL return `False` (fail-closed)

#### Scenario: Expression referencing undefined variable fails closed

- **WHEN** `evaluator.evaluate("user_role == 'admin'", {})` is called (no `user_role` in context)
- **THEN** the expression SHALL raise `NameError` and the evaluator SHALL return `False`

#### Scenario: Syntax error in expression fails closed

- **WHEN** `evaluator.evaluate("user_role == ", {"user_role": "admin"})` is called
- **THEN** the expression SHALL raise `SyntaxError` and the evaluator SHALL return `False`

### Requirement: ToolGateEvaluator filters tool list

The `ToolGateEvaluator` SHALL provide a `filter_tools(tools, context)` method that accepts a list of tool dicts and a runtime context dict, and returns a new list containing only tools whose `available_when` evaluates to `True` (or has no `available_when`).

#### Scenario: Mixed tools with and without available_when

- **WHEN** `filter_tools` receives 3 tools: one with `available_when=None`, one with `available_when="user_role == 'admin'"` (True), one with `available_when="user_role == 'admin'"` (False)
- **THEN** the result SHALL contain exactly 2 tools (the always-available one and the one that evaluates True)

#### Scenario: All tools filtered out

- **WHEN** `filter_tools` receives 3 tools, all with `available_when` evaluating to `False`
- **THEN** the result SHALL be an empty list

#### Scenario: Empty tool list passthrough

- **WHEN** `filter_tools` receives an empty list
- **THEN** the result SHALL be an empty list

### Requirement: LLMWorker filters tools before LLM invocation

`LLMWorker.execute()` and `execute_stream()` SHALL filter the tool list using `ToolGateEvaluator` after extracting tools from `node_config` and before passing tools to `PreLLMHook`, `context_assemble`, or `llm_invoke`.

#### Scenario: Filtered tools reach the LLM

- **WHEN** a node has 5 tools, 2 of which have `available_when` that evaluates to `False`
- **THEN** the `llm_invoke` call SHALL receive only 3 tools in its config

#### Scenario: PreLLMHook sees filtered tools

- **WHEN** tools are filtered by `available_when`
- **THEN** the `PreLLMHook.on_pre_llm_call()` SHALL receive the filtered tool list, not the original

#### Scenario: No available_when on any tools — behavior unchanged

- **WHEN** none of the tools have `available_when` set
- **THEN** the tool list SHALL pass through unchanged (backward compatible)

### Requirement: Runtime context is assembled from execution_context and channel_snapshot

The runtime context for `available_when` evaluation SHALL be assembled by merging relevant keys from `execution_context` and `channel_snapshot` into a flat dict. The following variables SHALL be available when present in the source data:

- `session_id` (from execution_context)
- `superstep` (from execution_context)
- `user_id` (from channel_snapshot `_user_id`)
- `agent_id` (from channel_snapshot `_agent_id`)
- `turn_index` (from channel_snapshot `_turn_index`)

Additional derived variables (`phase`, `budget_remaining`, `user_role`) MAY be added when the corresponding features (Task Phase Detection 4.9, Token Budget 4.10, RBAC) are active.

#### Scenario: Basic context variables available

- **WHEN** `LLMWorker.execute()` runs with `execution_context={"session_id": "abc", "superstep": 3}` and `channel_snapshot={"_user_id": "user123", "_turn_index": 5}`
- **THEN** the runtime context for `available_when` SHALL contain `session_id="abc"`, `superstep=3`, `user_id="user123"`, `turn_index=5`

#### Scenario: Missing channel_snapshot keys are omitted

- **WHEN** `channel_snapshot` does not contain `_user_id`
- **THEN** the runtime context SHALL NOT contain `user_id` key (expressions referencing it will fail-closed)

### Requirement: Tool CRUD schemas support available_when field

`ToolCreateSchema` and `ToolUpdateSchema` SHALL accept an optional `available_when: str | None` field. `ToolReadSchema` SHALL include the `available_when` field in its output.

#### Scenario: Create tool with available_when

- **WHEN** `POST /api/tools` is called with `{"name": "admin_delete", "available_when": "user_role == 'admin'", ...}`
- **THEN** the tool SHALL be created with the `available_when` field stored in the database

#### Scenario: Create tool without available_when

- **WHEN** `POST /api/tools` is called without `available_when` in the request body
- **THEN** the tool SHALL be created with `available_when=None` (always available)

#### Scenario: Read tool includes available_when

- **WHEN** `GET /api/tools/{id}` is called for a tool with `available_when="user_role == 'admin'"`
- **THEN** the response SHALL include `"available_when": "user_role == 'admin'"`
