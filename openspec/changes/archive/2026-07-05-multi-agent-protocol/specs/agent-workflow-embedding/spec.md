## ADDED Requirements — 新增需求

### Requirement: Agent 可以将工作流作为工具调用 — Agents can invoke workflows as tools
系统应使 Agent 能够通过 `EnginePort.workflow_execute()` 方法将已注册的工作流作为可调用工具调用，允许多步逻辑被封装并在 Agent 间重用。

#### Scenario: Agent 将工作流作为工具调用 — Agent invokes a workflow as a tool
- **WHEN** Agent 的 LLM 生成针对工作流类型技能的工具调用
- **THEN** 系统调用 `EnginePort.workflow_execute(workflow_id, input, context)`，通过 PregelRuntime 执行工作流并返回结果

#### Scenario: 工作流工具参数自动生成 — Workflow tool parameters auto-generated
- **WHEN** 工作流被注册为技能
- **THEN** 系统生成工作流输入变量的 JSON Schema（来自 Start Node 配置），并将其暴露为工具的参数模式

### Requirement: 工作流可以将 Agent 嵌入为 DAG 节点 — Workflows can embed agents as DAG nodes
系统应支持工作流 DAG 中的 AGENT 类型节点，通过 `EnginePort.agent_execute()` 委托执行到指定的 Agent，带通道状态透传。

#### Scenario: 具有 Agent 节点的工作流 — Workflow with agent node
- **WHEN** 工作流 DAG 包含 `agent_id: "<uuid>"` 的 AGENT 节点
- **THEN** AgentWorker 应使用 Agent ID、来自传入通道的消息和作为上下文的通道快照调用 `EnginePort.agent_execute()`

#### Scenario: Agent 节点传递通道状态 — Agent node passes channel state
- **WHEN** AGENT 节点在工作流中执行
- **THEN** Agent 应接收当前通道快照作为上下文，Agent 的响应应写入节点的输出通道

### Requirement: 递归嵌套限制为深度 3 — Recursive nesting is limited to depth 3
系统应对 Agent → 工作流 → Agent 链强制执行最大嵌套深度 3，超出限制时抛出清晰错误。

#### Scenario: 在限制内的嵌套 — Nesting within limit
- **WHEN** Agent A 调用工作流 W1，其中包含 Agent B，Agent B 调用工作流 W2（深度 2）
- **THEN** 系统成功执行该链

#### Scenario: 嵌套超过限制 — Nesting exceeds limit
- **WHEN** 嵌套深度达到 4（Agent → WF → Agent → WF → Agent → WF → Agent）
- **THEN** 系统抛出 `NestingDepthExceededError`，消息指示最大深度为 3 和当前深度

#### Scenario: 通过上下文跟踪深度 — Depth tracking via context
- **WHEN** Agent 或工作流被调用
- **THEN** 系统在执行上下文中跟踪当前嵌套深度，在每个 Agent → 工作流或工作流 → Agent 转换时递增

### Requirement: WorkflowTool 将工作流包装为 Agent 可调用的工具 — WorkflowTool wraps workflows as agent-callable tools
系统应提供 `WorkflowTool` 类（类似于 `AgentTool`），包装工作流 ID 并将其暴露为 LLM 调用的可调用工具。

#### Scenario: WorkflowTool 生成工具模式 — WorkflowTool generates tool schema
- **WHEN** 为具有输入变量 `query` 和 `context` 的工作流 W1 创建 WorkflowTool
- **THEN** 工具的 JSON Schema 应有 `name: "workflow_<wf_name>"`、来自工作流的 `description`，以及与输入变量匹配的 `parameters`

#### Scenario: WorkflowTool 执行工作流 — WorkflowTool executes workflow
- **WHEN** LLM 调用带参数的 WorkflowTool
- **THEN** 工具委托给 `EnginePort.workflow_execute()`，包含工作流 ID、参数作为输入、当前通道快照作为上下文

### Requirement: 工作流执行重用现有的 PregelRuntime — Workflow execution reuses existing PregelRuntime
系统应使用现有的 PregelRuntime 和 GraphCompiler 基础设施执行嵌入的工作流，确保所有 guardrails、tracing 和事件存储日志适用于嵌套工作流执行。

#### Scenario: 嵌入的工作流触发 tracing — Embedded workflow triggers tracing
- **WHEN** Agent 通过 WorkflowTool 调用工作流
- **THEN** 工作流执行应在父 Agent 的 trace 下创建 trace spans，在 Full-Chain Tracing 系统中可见

#### Scenario: 嵌入的工作流遵守 guardrails — Embedded workflow respects guardrails
- **WHEN** 嵌入的工作流包含 LLM 节点
- **THEN** 现有的 PreLLMHook 和 PostLLMHook guardrails 应为嵌入的 LLM 调用触发
