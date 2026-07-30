## ADDED Requirements — 新增需求

### Requirement: A2A 服务器在知名端点提供 AgentCard — A2A Server serves AgentCard at well-known endpoint
系统应在 `/.well-known/agent-card.json` 提供 A2A AgentCard，描述 Hecate 实例的能力、技能、安全方案和受支持的接口。

#### Scenario: 从知名 URL 获取 AgentCard — Fetch AgentCard from well-known URL
- **WHEN** 任何 HTTP 客户端发送 `GET /.well-known/agent-card.json`
- **THEN** 系统返回 JSON AgentCard，包含 `name`、`description`、`version`、`url`、`capabilities`、`skills` 和 `securitySchemes` 字段

#### Scenario: AgentCard 反映已配置的能力 — AgentCard reflects configured capabilities
- **WHEN** 服务器启用了流式和推送通知
- **THEN** AgentCard 的 `capabilities` 对象应有 `streaming: true` 和 `pushNotifications: true`

#### Scenario: AgentCard 列出注册表中的 Agent 技能 — AgentCard lists agent skills from registry
- **WHEN** Agent 关联了 3 个工具和 2 个知识库
- **THEN** AgentCard 的 `skills` 数组应包含来自 Agent 的 SkillRegistry 解析的条目

### Requirement: A2A 服务器处理 SendMessage JSON-RPC 方法 — A2A Server handles SendMessage JSON-RPC method
系统应在 A2A 端点接受 `SendMessage` JSON-RPC 2.0 请求，并返回代表委托工作的 Task 对象。

#### Scenario: 发送消息创建任务 — Send message creates task
- **WHEN** A2A 客户端发送带用户消息部分的 `SendMessage`
- **THEN** 系统创建状态为 `TASK_STATE_SUBMITTED` 的任务，转换为 `TASK_STATE_WORKING`，执行 Agent，并返回最终状态为 `TASK_STATE_COMPLETED` 的 Task

#### Scenario: 向不存在的 Agent 发送消息 — Send message to non-existent agent
- **WHEN** A2A 客户端发送针对不存在的 Agent 的 `SendMessage`
- **THEN** 系统返回 JSON-RPC 错误，代码 `-32001`，消息 "Agent not found"

### Requirement: A2A 服务器使用 SSE 处理 SendStreamingMessage — A2A Server handles SendStreamingMessage with SSE
系统应接受 `SendStreamingMessage` 请求，并返回 Server-Sent Events 流，发出 TaskStatusUpdateEvent 和 TaskArtifactUpdateEvent 对象。

#### Scenario: 流式消息发出状态更新 — Streaming message emits status updates
- **WHEN** A2A 客户端发送 `SendStreamingMessage`
- **THEN** 系统发出 SSE 事件，`event: task/status`，包含 TaskStatusUpdateEvent 对象，以 `final: true` 的最终事件结束

#### Scenario: 流式消息发出 artifacts — Streaming message emits artifacts
- **WHEN** Agent 在执行期间产生 artifacts
- **THEN** 系统发出 SSE 事件，`event: task/artifact`，包含带 `append` 和 `lastChunk` 标志的 TaskArtifactUpdateEvent 对象

### Requirement: A2A 服务器处理 GetTask JSON-RPC 方法 — A2A Server handles GetTask JSON-RPC method
系统应接受 `GetTask` 请求，并返回当前的 Task 对象，包括状态、artifacts 和历史。

#### Scenario: 获取现有任务 — Get existing task
- **WHEN** A2A 客户端发送带有效任务 ID 的 `GetTask`
- **THEN** 系统返回 Task 对象，包含当前状态、累积的 artifacts 和对话历史

#### Scenario: 获取不存在的任务 — Get non-existent task
- **WHEN** A2A 客户端发送带未知任务 ID 的 `GetTask`
- **THEN** 系统返回 JSON-RPC 错误，代码 `-32001`

### Requirement: A2A 服务器处理 CancelTask JSON-RPC 方法 — A2A Server handles CancelTask JSON-RPC method
系统应接受 `CancelTask` 请求，并将任务转换为 `TASK_STATE_CANCELED`。

#### Scenario: 取消工作中的任务 — Cancel working task
- **WHEN** A2A 客户端发送针对 `TASK_STATE_WORKING` 状态任务的 `CancelTask`
- **THEN** 系统取消任务，停止 Agent 执行，并返回状态为 `TASK_STATE_CANCELED` 的 Task

#### Scenario: 取消终结状态任务失败 — Cancel terminal task fails
- **WHEN** A2A 客户端发送针对已处于 `TASK_STATE_COMPLETED` 状态任务的 `CancelTask`
- **THEN** 系统返回 JSON-RPC 错误，指示任务已处于终结状态

### Requirement: A2A 服务器持久化任务生命周期 — A2A Server persists task lifecycle
系统应将所有 A2A 任务持久化到数据库表（`a2a_tasks`）中，包含完整的状态转换历史。

#### Scenario: 任务状态转换被记录 — Task state transitions are recorded
- **WHEN** 任务从 SUBMITTED → WORKING → COMPLETED 转换
- **THEN** 数据库应包含一条包含所有三个带时间戳的状态转换的任务记录

### Requirement: A2A 客户端通过 AgentCard 发现远程 Agent — A2A Client discovers remote agents via AgentCard
系统应提供 A2AClient，从远程 `/.well-known/agent-card.json` 端点获取和解析 AgentCard。

#### Scenario: 发现远程 Agent — Discover remote agent
- **WHEN** A2AClient 被给定远程 URL `https://remote-agent.example.com`
- **THEN** 客户端获取 `https://remote-agent.example.com/.well-known/agent-card.json` 并返回解析后的 AgentCard 对象

#### Scenario: 带签名验证的发现 — Discovery with signature verification
- **WHEN** 远程 AgentCard 包含签名且验证已启用
- **THEN** 客户端应在返回 AgentCard 前验证签名，并拒绝签名无效的卡片

### Requirement: A2A 客户端向远程 Agent 提交任务 — A2A Client submits tasks to remote agents
系统应提供 A2AClient 方法，用于提交任务（`send_message`）、流式结果（`send_streaming_message`）、查询状态（`get_task`）和取消（`cancel_task`）。

#### Scenario: 向远程 Agent 提交任务 — Submit task to remote agent
- **WHEN** A2AClient 调用 `send_message(agent_url, message)`
- **THEN** 客户端发送 `SendMessage` JSON-RPC 请求并返回 Task 结果

#### Scenario: 从远程 Agent 流式获取任务 — Stream task from remote agent
- **WHEN** A2AClient 调用 `send_streaming_message(agent_url, message)`
- **THEN** 客户端返回 TaskStatusUpdateEvent 和 TaskArtifactUpdateEvent 对象的异步迭代器

### Requirement: A2A 服务器支持 APIKey 和 HTTP Bearer 认证 — A2A Server supports APIKey and HTTP Bearer authentication
系统应支持 A2A 端点的 APIKey（头）和 HTTP Bearer（JWT）认证方案，通过现有的 Hecate 认证基础设施进行验证。

#### Scenario: 有效的 APIKey 认证 — Valid APIKey authentication
- **WHEN** A2A 客户端发送带 `X-API-Key: <valid_key>` 头的请求
- **THEN** 系统认证该请求并处理 A2A 操作

#### Scenario: 缺少认证凭据 — Missing authentication credentials
- **WHEN** A2A 客户端发送不带凭据的请求到受保护的端点
- **THEN** 系统返回 HTTP 401，带 `WWW-Authenticate` 头

### Requirement: A2A 协议与现有的 EnginePort 集成 — A2A protocol integrates with existing EnginePort
系统应通过现有的 EnginePort.agent_execute() 或工作流执行流水线路由 A2A 任务执行，确保所有现有的 guardrails、tracing 和审计日志适用。

#### Scenario: A2A 任务触发 guardrail 钩子 — A2A task triggers guardrail hooks
- **WHEN** A2A SendMessage 触发 Agent 执行
- **THEN** 现有的 PreLLMHook、PostLLMHook、PreToolHook、PostToolHook guardrails 应在执行期间触发

#### Scenario: A2A 任务出现在 tracing 中 — A2A task appears in tracing
- **WHEN** A2A 任务完成
- **THEN** 现有的 Full-Chain Tracing 系统应包含 A2A 发起的执行的 spans
