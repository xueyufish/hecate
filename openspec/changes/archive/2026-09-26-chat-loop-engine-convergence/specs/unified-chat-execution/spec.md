# unified-chat-execution Delta

## Purpose

将配置了工具的 agent 的 chat 执行收敛到统一 Pregel 运行时：一条执行路径产生一致的工具事件、审批记录、结束原因与恢复语义，HTTP 层只做协议适配。经功能开关渐进迁移，直连循环保留为回退。

## ADDED Requirements

### Requirement: 有工具 agent 经统一运行时执行

启用 `CHAT_TOOL_LOOP_ENGINE_ENABLED` 时，配置了工具的 agent 的 chat 请求（流式与非流式）SHALL 经 `WorkflowExecutionService` 与 Pregel chat 图执行，产生引擎事件（含 `TOOL_CALL`/`TOOL_RESULT` 回执）；开关关闭时 SHALL 保持直连循环行为不变。引擎 ToolWorker 的 guardrail 装配 SHALL 与直连路径等价（`middleware_chains`、`denial_tracker`、`event_store` 完整下传）。带知识库或增强功能的请求 SHALL NOT 丢失 agent 配置的工具。

#### Scenario: 开关开启时有工具 agent 走引擎

- **WHEN** `CHAT_TOOL_LOOP_ENGINE_ENABLED=true` 且配置了工具的 agent 发起 chat 请求
- **THEN** 执行经 Pregel 图完成，事件日志含配对的 `TOOL_CALL`/`TOOL_RESULT`

#### Scenario: 开关关闭时行为不变

- **WHEN** 开关为默认值 `false`
- **THEN** 有工具 agent 的 chat 请求走直连循环，行为与迁移前一致

#### Scenario: 增强分支不再丢失工具

- **WHEN** 带工具的 agent 携带 `kb_ids` 发起请求（开关开启）
- **THEN** agent 配置的工具在执行中可用（不再仅客户端工具）

### Requirement: 流式中间迭代静默

开关开启的流式 chat SHALL 保持既有可观察语义：中间工具迭代的 LLM 输出 SHALL NOT 作为内容块流式下发，仅最终答案流式输出；工具执行进度不冒充内容块。结束原因（finish_reason）语义与直连路径一致。

#### Scenario: 中间迭代不出现在流式内容中

- **WHEN** 一次需要两轮工具调用的流式 chat 完成
- **THEN** SSE 内容块仅包含最终答案文本，中间迭代的 LLM 文本（如有）不下发

#### Scenario: 结束原因一致

- **WHEN** 同一请求分别在开关开启与关闭下执行至自然结束
- **THEN** 两者的 finish_reason 语义一致（stop / tool-calls 耗尽等对应关系明确）

### Requirement: 跨入口执行一致性

同一 agent 的同一请求经 HTTP、MCP（`agent_chat`）、workflow 三入口执行时，SHALL 产生一致的工具事件序列、审批记录与结束原因——差异仅在传输适配层。审批在流式路径保持同步问询语义（引擎 interrupt 语义的流式表达留作后续演进）。

#### Scenario: HTTP 与 MCP 工具事件一致

- **WHEN** 同一配置工具的 agent 分别经 HTTP chat 与 MCP `agent_chat` 执行相同输入
- **THEN** 两者的事件日志含相同序列的 `TOOL_CALL`/`TOOL_RESULT`（tool_name 与 execution 配对语义一致）

#### Scenario: 审批记录一致

- **WHEN** 需审批的工具调用分别在开关开启的 HTTP 路径与引擎路径（MCP）执行
- **THEN** 审批记录（APPROVAL_ASKED/DECIDED 语义）等价产生
