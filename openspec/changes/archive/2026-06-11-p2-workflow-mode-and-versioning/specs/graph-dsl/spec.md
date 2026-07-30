## MODIFIED Requirements — 修改的需求

### Requirement: Compiler validates entry point, edges, and handoff cycles — 编译器验证入口点、边和 Handoff 循环
`GraphCompiler.compile()` 应在生成 `CompiledGraph` 之前执行验证阶段：入口点、边、handoff 循环、fan-out/merge 结构约束以及执行模式感知的节点限制。当 `execution_mode="task"` 传递给 compile() 时，编译器应通过抛出 `GraphValidationError` 来拒绝包含 INTERRUPT 或 SUGGESTION 节点类型的图谱。

#### Scenario: Entry point not found — 未找到入口点
- **当** 声明的入口点引用了一个不存在的节点
- **则** 应抛出 `GraphValidationError`，field="entry"

#### Scenario: Edge target references non-existent node — 边目标引用不存在的节点
- **当** 边目标既不是已声明的节点 ID 也不是哨兵（`__start__`、`__end__`）
- **则** 应抛出 `GraphValidationError`，字段指示边路径

#### Scenario: Unreachable nodes logged as warning — 不可达节点记录为警告
- **当** 存在无法通过 BFS 从入口点到达的节点
- **则** 编译器应记录一个包含不可达节点 ID 的 WARNING，但不应抛出错误

#### Scenario: Handoff between non-agent nodes — 非 Agent 节点间的 Handoff
- **当** handoff 边的源或目标不是 AGENT 类型的节点
- **则** 应抛出 `GraphValidationError`

#### Scenario: Fan-out without merge — Fan-out 无 Merge
- **当** 图谱包含 FAN_OUT 节点，但从其任何分支都无法到达 MERGE 节点
- **则** 应抛出 `GraphValidationError`，消息为 "FAN_OUT node '{id}' has no reachable MERGE node"

#### Scenario: Merge without fan-out — Merge 无 Fan-out
- **当** 图谱包含 MERGE 节点，但上游没有 FAN_OUT 节点
- **则** 应抛出 `GraphValidationError`，消息为 "MERGE node '{id}' has no upstream FAN_OUT node"

#### Scenario: Task mode with INTERRUPT node — 任务模式包含 INTERRUPT 节点
- **当** `execution_mode="task"` 传递给 `compile()` 且图谱包含 INTERRUPT 节点
- **则** 编译器应抛出 `GraphValidationError`，消息指示 INTERRUPT 节点在任务模式下被禁止

#### Scenario: Task mode with SUGGESTION node — 任务模式包含 SUGGESTION 节点
- **当** `execution_mode="task"` 传递给 `compile()` 且图谱包含 SUGGESTION 节点
- **则** 编译器应抛出 `GraphValidationError`，消息指示 SUGGESTION 节点在任务模式下被禁止

#### Scenario: Conversational mode allows all node types — 对话模式允许所有节点类型
- **当** `execution_mode="conversational"` 传递给 `compile()` 且图谱包含 INTERRUPT 和 SUGGESTION 节点
- **则** 编译器应成功编译，不抛出与模式相关的错误

#### Scenario: No execution mode defaults to conversational — 未指定执行模式默认使用对话模式
- **当** 未向 `compile()` 提供 `execution_mode`
- **则** 编译器应默认采用 `"conversational"` 行为，允许所有节点类型
