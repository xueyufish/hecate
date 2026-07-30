## ADDED Requirements — 新增需求

### Requirement: Workflow execution mode field — 工作流执行模式字段
`WorkflowModel` 应包含一个 `execution_mode` 字段，允许的值为 `conversational` 和 `task`。默认值应为 `conversational`。该字段应包含在 `WorkflowCreateSchema`、`WorkflowUpdateSchema` 和 `WorkflowReadSchema` 中。

#### Scenario: Create workflow with default mode — 使用默认模式创建工作流
- **当** 创建工作流时未指定 `execution_mode`
- **则** 工作流的 `execution_mode` 应设置为 `"conversational"`

#### Scenario: Create workflow with explicit task mode — 使用显式任务模式创建工作流
- **当** 使用 `execution_mode="task"` 创建工作流
- **则** 工作流应将 `"task"` 存储为其执行模式

#### Scenario: Update workflow execution mode — 更新工作流执行模式
- **当** 工作流的 `execution_mode` 从 `"conversational"` 更新为 `"task"`
- **则** 更新后的值应被持久化
- **并且** 后续编译应应用任务模式验证规则

### Requirement: Task mode forbids interaction nodes at compile time — 任务模式在编译时禁止交互节点
`GraphCompiler.compile()` 应接受一个可选的 `execution_mode` 参数。当 `execution_mode="task"` 时，编译器应通过抛出 `GraphValidationError` 来拒绝包含 INTERRUPT 或 SUGGESTION 节点类型的图谱。

#### Scenario: Task mode graph with INTERRUPT node — 任务模式图谱包含 INTERRUPT 节点
- **当** 一个 `execution_mode="task"` 的图谱包含类型为 `INTERRUPT` 的节点
- **则** `GraphCompiler.compile()` 应抛出 `GraphValidationError`，消息指示 INTERRUPT 节点在任务模式下被禁止

#### Scenario: Task mode graph with SUGGESTION node — 任务模式图谱包含 SUGGESTION 节点
- **当** 一个 `execution_mode="task"` 的图谱包含类型为 `SUGGESTION` 的节点
- **则** `GraphCompiler.compile()` 应抛出 `GraphValidationError`，消息指示 SUGGESTION 节点在任务模式下被禁止

#### Scenario: Conversational mode graph with INTERRUPT node — 对话模式图谱包含 INTERRUPT 节点
- **当** 一个 `execution_mode="conversational"` 的图谱包含类型为 `INTERRUPT` 的节点
- **则** `GraphCompiler.compile()` 应成功编译，不抛出错误

#### Scenario: Task mode graph without interaction nodes — 任务模式图谱不包含交互节点
- **当** 一个 `execution_mode="task"` 的图谱仅包含 CONVERSATION、TOOL_CALL、CONDITION、KNOWLEDGE_RETRIEVAL、VARIABLE_SET、FAN_OUT、MERGE 和 AGENT 节点
- **则** `GraphCompiler.compile()` 应成功编译

### Requirement: Runtime behavior differentiation by execution mode — 按执行模式区分的运行时行为
`PregelRuntime.execute()` 应根据工作流的 `execution_mode` 调整行为。在任务模式下，检查点应被禁用，`StreamMode` 应仅限制为 `VALUES`。在对话模式下，检查点应启用，所有 `StreamMode` 值均应支持。

#### Scenario: Task mode disables checkpointing — 任务模式禁用检查点
- **当** 执行 `execution_mode="task"` 的工作流
- **则** PregelRuntime 不应在超步之间持久化检查点
- **并且** 运行时不应接受用于状态恢复的 `conversation_id`

#### Scenario: Task mode limits stream mode — 任务模式限制流模式
- **当** 使用 `stream_mode=StreamMode.MESSAGES` 执行 `execution_mode="task"` 的工作流
- **则** 运行时应覆盖为 `StreamMode.VALUES` 并继续执行

#### Scenario: Conversational mode enables checkpointing — 对话模式启用检查点
- **当** 执行 `execution_mode="conversational"` 的工作流
- **则** PregelRuntime 应持久化检查点并支持中断/恢复

### Requirement: Execution mode system variables — 执行模式系统变量
引擎应提供执行模式感知的系统变量。`sys.execution_mode` 应在所有模式下可用。`sys.conversation_id` 和 `sys.dialogue_count` 仅应在对话模式下设置。

#### Scenario: System variables in conversational mode — 对话模式下的系统变量
- **当** 使用 `conversation_id="conv-123"` 执行对话工作流，且这是第 5 条消息
- **则** 通道状态应包含 `sys.execution_mode="conversational"`、`sys.conversation_id="conv-123"` 和 `sys.dialogue_count=5`

#### Scenario: System variables in task mode — 任务模式下的系统变量
- **当** 执行任务工作流
- **则** 通道状态应包含 `sys.execution_mode="task"`
- **并且** `sys.conversation_id` 和 `sys.dialogue_count` 不应存在
