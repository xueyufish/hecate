## ADDED Requirements — 新增需求

### Requirement: Per-Session Sequential Processing — 需求：每会话顺序处理
系统应确保在单个对话/会话内一次只处理一条消息。当新消息到达已在处理的会话时，系统应将该消息排队，并在当前消息完成后处理。

#### Scenario: Single message processing — 场景：单条消息处理
- **WHEN** 用户向空闲会话发送消息
- **THEN** 消息应立即处理，无需排队

#### Scenario: Concurrent messages queued — 场景：并发消息排队
- **WHEN** 用户在消息 A 仍在处理时向同一会话发送消息 B
- **THEN** 消息 B 应排队，在消息 A 完成后处理

#### Scenario: Multiple queued messages — 场景：多条排队消息
- **WHEN** 消息 B、C、D 在消息 A 处理时排队
- **THEN** 应按 FIFO 顺序处理：A → B → C → D

### Requirement: Queue Status Feedback — 需求：队列状态反馈
当消息排队或处理时，系统应在响应头中返回队列状态信息。

#### Scenario: Message processing immediately — 场景：消息立即处理
- **WHEN** 消息无需等待即被处理
- **THEN** 响应应包含头 `X-Queue-Position: 0`

#### Scenario: Message queued — 场景：消息已排队
- **WHEN** 消息排在 N 条其他消息之后
- **THEN** 响应应包含头 `X-Queue-Position: N` 和 `X-Queue-Wait-Ms: <milliseconds>`

### Requirement: Queue Timeout — 需求：队列超时
排队消息应在 5 分钟后超时。如果消息在队列中等待超过超时时间，系统应返回 HTTP 408 Request Timeout。

#### Scenario: Message times out in queue — 场景：消息在队列中超时
- **WHEN** 消息在队列中等待超过 5 分钟
- **THEN** 系统应返回 HTTP 408，带队列超时消息

#### Scenario: Message processes within timeout — 场景：消息在超时前处理
- **WHEN** 消息在 5 分钟内出队并处理
- **THEN** 系统应正常处理，无超时错误

### Requirement: Different Sessions Independent — 需求：不同会话独立
不同会话的消息应独立处理，互不阻塞。繁忙的会话 A 不应阻塞会话 B。

#### Scenario: Independent sessions — 场景：独立会话
- **WHEN** 会话 A 正在处理长消息，同时会话 B 收到新消息
- **THEN** 会话 B 的消息应立即处理，无需等待会话 A

### Requirement: Queue Indicator in Chat UI — 需求：聊天 UI 中的队列指示器
前端聊天页面应在消息排队时显示队列指示器。指示器应显示队列位置，并在消息处理时更新。

#### Scenario: Message queued in chat — 场景：聊天中消息排队
- **WHEN** 用户发送消息且该消息被排队（X-Queue-Position > 0）
- **THEN** 聊天 UI 应显示"Queued (position N)..."指示器

#### Scenario: Message starts processing — 场景：消息开始处理
- **WHEN** 排队消息开始处理
- **THEN** 队列指示器应被移除，响应正常流式传输
