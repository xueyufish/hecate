## ADDED Requirements — 新增需求

### Requirement: ChannelABC 定义外部平台适配器接口 — ChannelABC defines external platform adapter interface
系统应在 `channel/adapter.py` 中定义一个 `ChannelABC` 抽象基类，包含以下抽象接口：`name` 属性、`description` 属性、返回 `ChannelCapabilities` 的 `capabilities` 属性、`receive(raw)` 方法、`respond(message_id, response)` 方法和 `stream(message_id, chunks)` 方法。

#### Scenario: 具体通道实现 — Concrete channel implementation
- **WHEN** 一个类继承 ChannelABC 并实现所有抽象方法
- **THEN** 它应可使用 `type="channel"` 注册到 PluginRegistry

#### Scenario: 缺少抽象方法 — Missing abstract method
- **WHEN** 一个类继承 ChannelABC 但未实现 `receive()`
- **THEN** 实例化应抛出 TypeError

### Requirement: CanonicalMessage 是通用消息格式 — CanonicalMessage is the universal message format
系统应定义一个冻结的 `CanonicalMessage` 数据类，包含字段：`id` (UUID)、`channel_id` (str)、`user_id` (str)、`session_id` (str | None)、`content` (MessageContent)、`metadata` (dict)、`timestamp` (datetime)。`MessageContent` 应包含 `text` (str | None) 和 `attachments`（Attachment 对象元组）。

#### Scenario: 从文本创建规范消息 — Create canonical message from text
- **WHEN** 使用 `content=MessageContent(text="hello")` 创建 CanonicalMessage
- **THEN** 它应是不可变的（冻结数据类）

#### Scenario: 元数据透传 — Metadata passthrough
- **WHEN** 使用 `metadata={"telegram_chat_id": "123"}` 创建 CanonicalMessage
- **THEN** 元数据字典应按原样保留，供下游平台特定逻辑使用

### Requirement: ChannelCapabilities 声明平台支持 — ChannelCapabilities declares platform support
系统应定义一个冻结的 `ChannelCapabilities` 数据类，包含布尔字段：`streaming`、`interactive_buttons`、`file_upload`、`markdown`、`rich_cards`，以及可选的 `max_message_length` (int | None)。所有布尔字段应默认为 `False`。

#### Scenario: 通道声明流式支持 — Channel declares streaming support
- **WHEN** 通道的 `capabilities` 属性返回 `ChannelCapabilities(streaming=True)`
- **THEN** Gateway 应对该通道的响应使用流式

#### Scenario: 不支持流式的通道 — Channel without streaming
- **WHEN** 通道的 `capabilities` 返回 `ChannelCapabilities(streaming=False)`
- **THEN** Gateway 应在发送前缓冲完整响应

### Requirement: Gateway 将消息从通道路由到 Agent 运行时 — Gateway routes messages from channels to agent runtime
系统应实现一个 `Gateway` 类，接受来自通道的 `CanonicalMessage`，解析会话上下文，并委托给 `WorkflowExecutionService`。Gateway 应是无状态的，不修改消息内容。

#### Scenario: Gateway 从 REST 通道接收消息 — Gateway receives message from REST channel
- **WHEN** RESTChannelAdapter 向 Gateway 发送 CanonicalMessage
- **THEN** Gateway 应解析会话（创建或恢复）并调用 WorkflowExecutionService

#### Scenario: Gateway 从未知通道接收消息 — Gateway receives message from unknown channel
- **WHEN** CanonicalMessage 到达时 `channel_id` 不匹配任何已注册的通道
- **THEN** Gateway 应抛出 ValueError

### Requirement: Gateway 会话路由 — Gateway session routing
Gateway 应维护 `session_id → (channel_id, user_id)` 的映射。当消息到达且具有现有的 `session_id` 时，Gateway 应将其路由到同一会话。当 `session_id` 为 None 时，Gateway 应创建新会话。

#### Scenario: 恢复现有会话 — Resume existing session
- **WHEN** CanonicalMessage 到达时 `session_id="abc"` 且之前由通道 "feishu" 创建
- **THEN** Gateway 应路由到现有会话，无论新消息来自哪个通道

#### Scenario: 创建新会话 — Create new session
- **WHEN** CanonicalMessage 到达时 `session_id=None`
- **THEN** Gateway 应创建新会话并分配 UUID

### Requirement: NotificationDispatcher 成为出站 Channel — NotificationDispatcher becomes outbound Channel
现有的 `NotificationDispatcher` 应重构为使用 Channel 适配器进行出站分发。每个通知目标（Email、飞书卡片、Slack Block Kit、通用 webhook）应成为 Channel 适配器，使用 `respond()` 方法发送告警通知。

#### Scenario: 通过飞书通道发送告警 — Send alert via Feishu channel
- **WHEN** AlertEvent 被分派到飞书通道适配器
- **THEN** 适配器应使用从告警渲染的飞书卡片负载调用 `respond()`

#### Scenario: 通过邮件通道发送告警 — Send alert via email channel
- **WHEN** AlertEvent 被分派到邮件通道适配器
- **THEN** 适配器应使用从告警渲染的 HTML 邮件调用 `respond()`

### Requirement: 迁移期间保留现有 REST API — Existing REST API preserved during migration
现有的 `api/v1/chat.py` 端点应按原样继续运行。应创建一个包装相同逻辑的 `RESTChannelAdapter`，允许逐步迁移到 Gateway 模式而不破坏 API 契约。

#### Scenario: 现有 REST API 继续工作 — Existing REST API continues to work
- **WHEN** 客户端发送 POST /v1/chat/completions
- **THEN** 响应应与当前行为相同（无变化）

#### Scenario: RESTChannelAdapter 可通过 Gateway 使用 — RESTChannelAdapter can be used via Gateway
- **WHEN** RESTChannelAdapter 注册到 Gateway
- **THEN** 来自 REST API 的消息应可通过 Gateway 路由

### Requirement: 通过 PluginRegistry 注册通道插件 — Channel plugin registration via PluginRegistry
通道适配器应使用 `type="channel"` 注册到 PluginRegistry。清单应包含通道的 `name`、`description` 和 `capabilities`。

#### Scenario: 注册新的通道适配器 — Register a new channel adapter
- **WHEN** 使用 `type="channel"` 调用 `registry.register(manifest, feishu_adapter)`
- **THEN** 适配器应可通过 `registry.get_by_name("feishu")` 检索

#### Scenario: 列出所有已注册的通道 — List all registered channels
- **WHEN** 调用 `registry.get_by_type("channel")`
- **THEN** 应返回所有已注册的通道适配器
