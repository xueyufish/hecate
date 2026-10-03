# a2a-protocol Delta

## MODIFIED Requirements

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
