## ADDED Requirements — 新增需求

### Requirement: AgentTool 支持远程 A2A Agent 目标 — AgentTool supports remote A2A agent targets
引擎应扩展 `AgentTool` 类以支持远程 A2A Agent 目标（除了本地 Agent ID），使 Agent 能够通过 A2AClient 将远程 A2A Agent 作为工具调用。

#### Scenario: 具有远程 A2A 目标的 AgentTool — AgentTool with remote A2A target
- **WHEN** 使用远程 Agent URL（而非本地 Agent UUID）创建 AgentTool
- **THEN** 工具的 `execute()` 方法应委托给 `A2AClient.send_message(remote_url, message)` 而非 `EnginePort.agent_execute()`

#### Scenario: 远程 Agent 超时 — Remote agent timeout
- **WHEN** 远程 A2A Agent 调用超过配置的超时时间
- **THEN** AgentTool 应返回错误字典，包含 `{"error": "Remote agent timed out", "timed_out": True}`

#### Scenario: 远程 Agent 错误传播 — Remote agent error propagation
- **WHEN** 远程 A2A Agent 返回状态为 `state: TASK_STATE_FAILED` 的任务
- **THEN** AgentTool 应从任务状态中提取错误消息，并将其作为工具执行错误返回

### Requirement: AgentTool 支持工作流目标 — AgentTool supports workflow targets
引擎应通过 `WorkflowTool` 类使 AgentTool 能够将工作流包装为可调用工具，提供与 AgentTool 并行的接口，但委托给 `EnginePort.workflow_execute()`。

#### Scenario: WorkflowTool 作为 Agent 可调用的工具 — WorkflowTool as agent-callable tool
- **WHEN** WorkflowTool 注册到 Agent 的工具列表中
- **THEN** LLM 应将工作流视为可调用工具，包含名称、描述和从工作流的 Start Node 变量派生的参数模式
