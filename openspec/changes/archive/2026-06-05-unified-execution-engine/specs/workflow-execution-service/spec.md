## ADDED Requirements — 新增需求

### Requirement: 所有 agent 模式的统一执行入口点 — Unified execution entry point for all agent modes
`WorkflowExecutionService` 应接受 AgentModel，根据 `agent.mode` 解析相应的图模板，编译图，实例化生产级 Workers，并通过 PregelRuntime 执行。所有三种模式（chat、three_layer、workflow）都应通过此服务路由。

#### Scenario: 聊天模式执行 — Chat mode execution
- **当** `execute(agent_mode="chat", messages=[...], model="gpt-4o", ...)` 被调用
- **则** 服务应调用 `build_chat_graph()` 生成 GraphConfig，编译它，创建生产级 Workers，并运行 PregelRuntime.execute()

#### Scenario: Three_layer 模式执行 — Three_layer mode execution
- **当** `execute(agent_mode="three_layer", messages=[...], ...)` 被调用
- **则** 服务应调用 `build_three_layer_graph()` 生成 GraphConfig，编译它，创建生产级 Workers，并运行 PregelRuntime.execute()

#### Scenario: Workflow 模式执行 — Workflow mode execution
- **当** `execute(agent_mode="workflow", workflow_id=<uuid>, messages=[...], ...)` 被调用
- **则** 服务应从数据库加载工作流的当前版本，调用 `parse_graph(version.graph_dsl)` 生成 GraphConfig，编译它，创建生产级 Workers，并运行 PregelRuntime.execute()

### Requirement: 通过执行服务的流式支持 — Streaming support through execution service
`WorkflowExecutionService` 应支持流式和非流式执行模式，映射到 PregelRuntime 的 StreamMode。

#### Scenario: 流式执行 — Streaming execution
- **当** `execute(stream=True)` 被调用
- **则** 服务应返回一个 AsyncGenerator，yield 来自 PregelRuntime 的事件，使用 StreamMode.MESSAGES

#### Scenario: 非流式执行 — Non-streaming execution
- **当** `execute(stream=False)` 被调用
- **则** 服务应消费 PregelRuntime 的生成器并将最终的通道状态作为响应字典返回

### Requirement: 会话和证据元数据传播 — Session and evidence metadata propagation
`WorkflowExecutionService` 应通过通道状态传播 session_id、agent_id、user_id 和 turn_index，以便 Workers 能够访问它们以进行证据跟踪、记忆操作和建议生成。

#### Scenario: 通道中的元数据 — Metadata in channels
- **当** 使用 session_id、agent_id、user_id 调用 execute
- **则** 服务应在 PregelRuntime 执行前将 `{"_session_id": ..., "_agent_id": ..., "_user_id": ..., "_turn_index": 0}` 注入到 initial_input 通道中
