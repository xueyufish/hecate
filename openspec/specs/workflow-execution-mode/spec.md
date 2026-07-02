## Purpose
Define execution mode behavior for workflows — conversational (multi-turn, checkpointed) vs task (single-shot, no checkpointing).

## Requirements

### Requirement: Workflow execution mode field
The `WorkflowModel` SHALL include an `execution_mode` field with allowed values `conversational` and `task`. The default value SHALL be `conversational`. The field SHALL be included in `WorkflowCreateSchema`, `WorkflowUpdateSchema`, and `WorkflowReadSchema`.

#### Scenario: Create workflow with default mode
- **WHEN** a workflow is created without specifying `execution_mode`
- **THEN** the workflow SHALL have `execution_mode` set to `"conversational"`

#### Scenario: Create workflow with explicit task mode
- **WHEN** a workflow is created with `execution_mode="task"`
- **THEN** the workflow SHALL store `"task"` as its execution mode

#### Scenario: Update workflow execution mode
- **WHEN** a workflow's `execution_mode` is updated from `"conversational"` to `"task"`
- **THEN** the updated value SHALL be persisted
- **AND** subsequent compilations SHALL apply task-mode validation rules

### Requirement: Task mode forbids interaction nodes at compile time
The `GraphCompiler.compile()` SHALL accept an optional `execution_mode` parameter. When `execution_mode="task"`, the compiler SHALL reject graphs containing INTERRUPT or SUGGESTION node types by raising `GraphValidationError`.

#### Scenario: Task mode graph with INTERRUPT node
- **WHEN** a graph with `execution_mode="task"` contains a node of type `INTERRUPT`
- **THEN** `GraphCompiler.compile()` SHALL raise `GraphValidationError` with a message indicating that INTERRUPT nodes are forbidden in task mode

#### Scenario: Task mode graph with SUGGESTION node
- **WHEN** a graph with `execution_mode="task"` contains a node of type `SUGGESTION`
- **THEN** `GraphCompiler.compile()` SHALL raise `GraphValidationError` with a message indicating that SUGGESTION nodes are forbidden in task mode

#### Scenario: Conversational mode graph with INTERRUPT node
- **WHEN** a graph with `execution_mode="conversational"` contains a node of type `INTERRUPT`
- **THEN** `GraphCompiler.compile()` SHALL compile successfully without raising an error

#### Scenario: Task mode graph without interaction nodes
- **WHEN** a graph with `execution_mode="task"` contains only CONVERSATION, TOOL_CALL, CONDITION, KNOWLEDGE_RETRIEVAL, VARIABLE_SET, FAN_OUT, MERGE, and AGENT nodes
- **THEN** `GraphCompiler.compile()` SHALL compile successfully

### Requirement: Runtime behavior differentiation by execution mode
The `PregelRuntime.execute()` SHALL adjust behavior based on the workflow's `execution_mode`. In task mode, checkpointing SHALL be disabled and `StreamMode` SHALL be limited to `VALUES` only. In conversational mode, checkpointing SHALL be enabled and all `StreamMode` values SHALL be supported.

#### Scenario: Task mode disables checkpointing
- **WHEN** a workflow with `execution_mode="task"` is executed
- **THEN** the PregelRuntime SHALL NOT persist checkpoints between supersteps
- **AND** the runtime SHALL NOT accept a `conversation_id` for state restoration

#### Scenario: Task mode limits stream mode
- **WHEN** a workflow with `execution_mode="task"` is executed with `stream_mode=StreamMode.MESSAGES`
- **THEN** the runtime SHALL override to `StreamMode.VALUES` and proceed with execution

#### Scenario: Conversational mode enables checkpointing
- **WHEN** a workflow with `execution_mode="conversational"` is executed
- **THEN** the PregelRuntime SHALL persist checkpoints and support interrupt/resume

### Requirement: Execution mode system variables
The engine SHALL provide execution-mode-aware system variables. `sys.execution_mode` SHALL be available in all modes. `sys.conversation_id` and `sys.dialogue_count` SHALL only be set in conversational mode.

#### Scenario: System variables in conversational mode
- **WHEN** a conversational workflow is executed with `conversation_id="conv-123"` and this is the 5th message
- **THEN** the channel state SHALL contain `sys.execution_mode="conversational"`, `sys.conversation_id="conv-123"`, and `sys.dialogue_count=5`

#### Scenario: System variables in task mode
- **WHEN** a task workflow is executed
- **THEN** the channel state SHALL contain `sys.execution_mode="task"`
- **AND** `sys.conversation_id` and `sys.dialogue_count` SHALL NOT be present
