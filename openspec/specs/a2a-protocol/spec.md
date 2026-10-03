# a2a-protocol Specification

## Purpose

Agent-to-Agent protocol integration: the Hecate server exposes Agent Cards, task lifecycle, and message methods over JSON-RPC/SSE, and the Hecate client discovers and invokes remote agents — with cryptographic card signing on the server side and fail-closed signature verification on the client side.

## Requirements

### Requirement: A2A Server serves AgentCard at well-known endpoint
The system SHALL serve an A2A AgentCard at `/.well-known/agent-card.json` describing the Hecate instance's capabilities, skills, security schemes, and supported interfaces.

#### Scenario: Fetch AgentCard from well-known URL
- **WHEN** any HTTP client sends `GET /.well-known/agent-card.json`
- **THEN** the system returns a JSON AgentCard with `name`, `description`, `version`, `url`, `capabilities`, `skills`, and `securitySchemes` fields

#### Scenario: AgentCard reflects configured capabilities
- **WHEN** the server has streaming and push notifications enabled
- **THEN** the AgentCard `capabilities` object SHALL have `streaming: true` and `pushNotifications: true`

#### Scenario: AgentCard lists agent skills from registry
- **WHEN** an agent has 3 tools and 2 knowledge bases associated
- **THEN** the AgentCard `skills` array SHALL contain entries derived from the agent's SkillRegistry resolution

### Requirement: A2A Server handles SendMessage JSON-RPC method
The system SHALL accept `SendMessage` JSON-RPC 2.0 requests at the A2A endpoint and return a Task object representing the delegated work.

#### Scenario: Send message creates task
- **WHEN** an A2A client sends `SendMessage` with a user message part
- **THEN** the system creates a task with state `TASK_STATE_SUBMITTED`, transitions to `TASK_STATE_WORKING`, executes the agent, and returns the final Task with state `TASK_STATE_COMPLETED`

#### Scenario: Send message to non-existent agent
- **WHEN** an A2A client sends `SendMessage` targeting a non-existent agent
- **THEN** the system returns a JSON-RPC error with code `-32001` and message "Agent not found"

### Requirement: A2A Server handles SendStreamingMessage with SSE
The system SHALL accept `SendStreamingMessage` requests and return a Server-Sent Events stream emitting TaskStatusUpdateEvent and TaskArtifactUpdateEvent objects.

#### Scenario: Streaming message emits status updates
- **WHEN** an A2A client sends `SendStreamingMessage`
- **THEN** the system emits SSE events with `event: task/status` containing TaskStatusUpdateEvent objects, ending with a final event where `final: true`

#### Scenario: Streaming message emits artifacts
- **WHEN** the agent produces artifacts during execution
- **THEN** the system emits SSE events with `event: task/artifact` containing TaskArtifactUpdateEvent objects with `append` and `lastChunk` flags

### Requirement: A2A Server handles GetTask JSON-RPC method
The system SHALL accept `GetTask` requests and return the current Task object including status, artifacts, and history.

#### Scenario: Get existing task
- **WHEN** an A2A client sends `GetTask` with a valid task ID
- **THEN** the system returns the Task object with current state, accumulated artifacts, and conversation history

#### Scenario: Get non-existent task
- **WHEN** an A2A client sends `GetTask` with an unknown task ID
- **THEN** the system returns a JSON-RPC error with code `-32001`

### Requirement: A2A Server handles CancelTask JSON-RPC method
The system SHALL accept `CancelTask` requests and transition the task to `TASK_STATE_CANCELED`.

#### Scenario: Cancel working task
- **WHEN** an A2A client sends `CancelTask` for a task in `TASK_STATE_WORKING`
- **THEN** the system cancels the task, stops agent execution, and returns the Task with state `TASK_STATE_CANCELED`

#### Scenario: Cancel terminal task fails
- **WHEN** an A2A client sends `CancelTask` for a task already in `TASK_STATE_COMPLETED`
- **THEN** the system returns a JSON-RPC error indicating the task is already in a terminal state

### Requirement: A2A Server persists task lifecycle
The system SHALL persist all A2A tasks in a database table (`a2a_tasks`) with full state transition history.

#### Scenario: Task state transitions are recorded
- **WHEN** a task transitions from SUBMITTED → WORKING → COMPLETED
- **THEN** the database SHALL contain a task record with all three state transitions timestamped

### Requirement: A2A Client discovers remote agents via AgentCard
The system SHALL provide an A2AClient that fetches and parses AgentCards from remote `/.well-known/agent-card.json` endpoints.

#### Scenario: Discover remote agent
- **WHEN** the A2AClient is given a remote URL `https://remote-agent.example.com`
- **THEN** the client fetches `https://remote-agent.example.com/.well-known/agent-card.json` and returns a parsed AgentCard object

#### Scenario: Discovery with signature verification
- **WHEN** the remote AgentCard contains signatures and verification is enabled
- **THEN** the client SHALL verify the signature before returning the AgentCard, and reject cards with invalid signatures

### Requirement: A2A Client submits tasks to remote agents
The system SHALL provide A2AClient methods to submit tasks (`send_message`), stream results (`send_streaming_message`), query status (`get_task`), and cancel (`cancel_task`).

#### Scenario: Submit task to remote agent
- **WHEN** the A2AClient calls `send_message(agent_url, message)`
- **THEN** the client sends a `SendMessage` JSON-RPC request and returns the Task result

#### Scenario: Stream task from remote agent
- **WHEN** the A2AClient calls `send_streaming_message(agent_url, message)`
- **THEN** the client returns an async iterator of TaskStatusUpdateEvent and TaskArtifactUpdateEvent objects

### Requirement: A2A Server supports APIKey and HTTP Bearer authentication
The system SHALL support APIKey (header) and HTTP Bearer (JWT) authentication schemes for A2A endpoints, validated against existing Hecate auth infrastructure.

#### Scenario: Valid APIKey authentication
- **WHEN** an A2A client sends a request with `X-API-Key: <valid_key>` header
- **THEN** the system authenticates the request and processes the A2A operation

#### Scenario: Missing authentication credentials
- **WHEN** an A2A client sends a request without credentials to a secured endpoint
- **THEN** the system returns HTTP 401 with a `WWW-Authenticate` header

### Requirement: A2A protocol integrates with existing EnginePort
A2A 任务执行 SHALL 经平台入口执行服务(消费共享装配的统一入口)发起,不得绕过执行服务直连模型调用;guardrail(hook 链)、审计与事件记录 SHALL 在 A2A 发起的执行上与其他入口同等生效。每次 A2A 任务执行 SHALL 关联平台 Task/Run 记录,workspace 归属取执行所用 agent 的 workspace。A2A 协议响应(Task/Artifact 字段、状态枚举)SHALL 保持协议兼容。

> 注:需求标识名保留历史命名(EnginePort),正文所指为现行平台入口执行服务;标识重命名留给后续规格清理。

#### Scenario: A2A task triggers guardrail hooks
- **WHEN** an A2A SendMessage triggers agent execution
- **THEN** the PreLLM/PostLLM/PreTool/PostTool guardrail hooks fire during execution, same as the HTTP chat entry

#### Scenario: A2A task appears in tracing
- **WHEN** an A2A task completes
- **THEN** the Full-Chain Tracing system contains spans for the A2A-initiated execution

#### Scenario: A2A 执行关联平台 Task/Run
- **WHEN** A2A SendMessage 经入口服务完成执行
- **THEN** 该执行登记了关联的 Task/Run,按所选 agent 的 workspace 归属,可按 agent 追溯

#### Scenario: A2A 响应 schema 保持协议兼容
- **WHEN** 迁移后的 A2A SendMessage 返回 Task 对象
- **THEN** Task/Artifact 字段集合与状态枚举与迁移前基准一致,A2A 客户端无需变更

### Requirement: 客户端卡片验签失败关闭

A2A 客户端发现远端 Agent Card 时，若调用方要求验签（`verify_signature=True`），系统 SHALL 接入 JWS 验签实现并按可信密钥来源（配置的受信 JWKS）执行验证：卡片无签名、签名验证失败（篡改）、或签发者不在受信集合时，发现过程 SHALL 以异常失败（不返回 Agent Card）。验签成功的卡片 SHALL 附带已验证标记。`verify_signature=False` 时卡片照常返回且明确处于"未验证"状态——调用方验证信任与服务端签名能力是两个独立维度，系统 SHALL 区分标注。

#### Scenario: 无签名卡片被拒绝

- **WHEN** `verify_signature=True` 且远端卡片不含 `signatures`
- **THEN** 发现过程抛出异常，不返回 Agent Card

#### Scenario: 篡改卡片被拒绝

- **WHEN** 卡片签名与内容不匹配（签名验证失败）
- **THEN** 发现过程抛出异常，不返回 Agent Card

#### Scenario: 未知签发者被拒绝

- **WHEN** 卡片由受信 JWKS 之外的密钥签署
- **THEN** 发现过程抛出异常，不返回 Agent Card

#### Scenario: 验签通过标记已验证状态

- **WHEN** 卡片通过受信 JWKS 验签成功
- **THEN** 返回的卡片携带已验证标记，供后续调用决策使用

#### Scenario: 未要求验签时明确未验证状态

- **WHEN** `verify_signature=False` 拉取任意卡片
- **THEN** 卡片正常返回且标记为未验证

### Requirement: A2A 执行的 agent 解析显式且按 workspace 界定

A2A 入口执行 SHALL 只解析显式配置范围内的目标 agent(配置声明的 workspace 作用域);解析结果 MUST 恰为一个该作用域内的 agent。未配置作用域、或作用域内无法唯一解析时,执行 MUST 以协议内失败状态拒绝,MUST NOT 回退到选取作用域之外的任何 agent(含按全局顺序取第一个);执行发生的任何归属(workspace、Task/Run 关联)取所解析 agent 行的 workspace。完整 per-caller/per-agent 身份仍是登记缺口,退出条件为显式的调用方→agent 映射;在该缺口关闭前,本要求的拒绝语义是过渡契约。

#### Scenario: 未配置作用域时拒绝执行

- **WHEN** A2A 服务器未配置 agent 解析的 workspace 作用域而收到 SendMessage
- **THEN** 返回协议内失败状态的 Task(或等价 JSON-RPC 错误),不执行任何 agent,失败原因为配置缺失而非内部错误

#### Scenario: 作用域外 agent 不可被选中

- **WHEN** 系统中存在配置作用域之外的 agent(包括其他 workspace 的 agent)
- **THEN** 这些 agent 不会被 A2A 执行路径选中,不存在按全局顺序回退选取

#### Scenario: 失败响应不泄露内部错误

- **WHEN** A2A 执行因内部异常失败并返回失败 Task
- **THEN** 面向协议客户端的消息为稳定的失败描述,不含异常堆栈、SQL/路径等内部细节;内部细节只进入服务端日志
