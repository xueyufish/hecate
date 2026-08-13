## Why

Hecate 当前的 channel 抽象(`src/hecate/channel/`)是空壳:`ChannelABC`、`CanonicalMessage`、`ChannelCapabilities` 接口已经定义,但 `register_channels()` 的内置通道列表为空,`Gateway.route()` 末尾写着 `TODO: Delegate to WorkflowExecutionService when integrating`,`SessionRouter` 是纯内存 dict。这意味着:任何在飞书/Slack 群里 @bot 想跟 Hecate Agent 对话的需求,目前都没有实现路径。

本次变更(MVP 范围)交付:飞书(Lark)和 Slack 两个 IM 平台的双向对话支持——`POST /v1/channels/{name}/webhook` 接收入站消息,通过现有的 `ChannelABC` 包装的薄层适配器归一化为 `CanonicalMessage`,经过身份绑定解析后接入 `WorkflowExecutionService.execute()`,把回复通过 `ChannelABC.respond/stream` 回传到 IM 平台。同时新增 IM 身份绑定数据模型(`im_identity_bindings`),强制每个 IM 用户先在 Web UI 完成绑定才能对话,绑定后同一 Hecate user 在不同通道的会话历史共享。

为什么现在做:Hecate 已有 channel 抽象骨架但缺入站实现,而飞书/Slack 是中国/国际企业 IM 的事实标准;开源参考(字节 deer-flow、阿里 AgentScope)都已经在 2025-2026 年完成了这条路径,业界方向明确收敛。同时 Hecate 多租户 + RBAC 模型跟 IM 身份绑定天然契合(`Workspace → User → RBAC` 已有),不抓住这个契合点会让后续 SCIM 集成更痛。

## What Changes

- **新增 IM 通道适配器** — 在 `src/hecate/channel/im/` 下新增 `FeishuChannel`(包装 `lark_oapi.channel.FeishuChannel`)和 `SlackChannel`(包装 `slack_bolt.App`),实现 `ChannelABC` 的 `receive/respond/stream` 三个方法
- **新增入站 webhook endpoint** — `POST /v1/channels/{name}/webhook`(FastAPI 路由),根据 `{name}` 查注册的 ChannelAdapter,把原始 payload 转发到 `ChannelABC.receive()` → `CanonicalMessage`
- **新增 IM 身份绑定数据模型** — 新表 `im_identity_bindings`(`workspace_id` + `user_id` + `channel_type` + `im_user_id` + `im_app_id` + 元数据),强制绑定流程
- **修改会话/消息数据模型** — `ConversationModel` 新增 `source_channel` 和 `im_chat_id` 字段;`MessageModel` 新增 `source_channel` 字段;`SessionModel` 新增 `source_channel` 字段(均 nullable,OpenAI 兼容 API 仍可用)
- **修改 Gateway 路由逻辑** — `SessionRouter` 持久化(替换内存 dict);`Gateway.route()` 接入 `WorkflowExecutionService.execute()`;新增 IM 身份解析步骤
- **新增异步执行解耦** — 新增 `MessageBus`(基于现有 `EventBus` 抽象或新建),webhook 在 200ms 内立即 ACK,Agent 实际执行放到后台 task,通过 `ChannelABC.stream()` 回流
- **新增绑定引导流程** — Web UI 端新增 "Link IM Account" 入口(POST `/v1/im/bindings/token`、`GET /v1/im/bindings/confirm`);绑定 token 通过现有 `vault/provider.py` 加密存储,默认 10 分钟过期、一次性使用

注意:本次**不**修改 `NotificationChannelAdapter`(出站告警)及其子类(`WebhookNotificationAdapter`、`WebSocketNotificationAdapter`、`EmailNotificationAdapter`)——告警系统维持现状。

注意:本次**不**实现钉钉、企业微信、Telegram 等其他 IM 平台——MVP 只交付飞书 + Slack 两个通道,但所有新增接口和模式为后续扩展留出空间。

注意:本次**不**做 "Channel as Tool" 维度(把 Slack/飞书封装成 MCP server 让 Agent 主动调用)—— 这属于后续 Phase。

## Capabilities

### New Capabilities

- `im-channel-feishu-slack`: 为 Hecate 增加飞书(Lark)和 Slack 两个 IM 平台的双向对话支持。涵盖:基于 `ChannelABC` 的薄层适配器实现,基于官方 SDK(`lark_oapi.channel`、`slack_bolt`)的 transport/签名/重连/去重自动委托;`POST /v1/channels/{name}/webhook` 入站路由;异步 MessageBus 解耦 webhook 立即 ACK 与 Agent 后台执行;流式输出(基础文本流式 + Phase 2 卡片流式)。

### Modified Capabilities

- `data-models`: 新增 `IMIdentityBindingModel` 表(强制 IM 身份绑定数据模型);`ConversationModel`、`MessageModel`、`SessionModel` 各新增 `source_channel` 字段用于追踪消息来源平台(IM 通道或 API);`ConversationModel` 新增 `im_chat_id` 字段用于回复路由。属于数据模型层面的扩展,影响所有现有按这些模型查询/序列化的代码,需要 delta 修改。

## Impact

**新增代码**:
- `src/hecate/channel/im/__init__.py`
- `src/hecate/channel/im/feishu.py`(FeishuChannel 实现)
- `src/hecate/channel/im/slack.py`(SlackChannel 实现)
- `src/hecate/channel/im/binding.py`(绑定 token 生成与校验)
- `src/hecate/channel/im/message_bus.py`(异步解耦)
- `src/hecate/channel/im/registry.py`(IM 通道注册,扩展现有 `register_channels()`)
- `src/hecate/models/im_identity_binding.py`(新 ORM 模型)
- `src/hecate/api/v1/channels.py`(webhook 入口 endpoint)
- `src/hecate/api/v1/im_bindings.py`(绑定 token 端点)
- `src/hecate/services/im_session_router.py`(替换现有 `gateway/session.py` 的内存 dict 实现)

**修改代码**:
- `src/hecate/models/conversation.py` — 加 `source_channel`、`im_chat_id` 字段
- `src/hecate/models/message.py` — 加 `source_channel` 字段
- `src/hecate/models/session.py` — 加 `source_channel` 字段
- `src/hecate/gateway/gateway.py` — `route()` 实现接入 `WorkflowExecutionService.execute()`
- `src/hecate/gateway/session.py` — 内存 dict 改为可插拔的持久化实现
- `src/hecate/services/workflow/execution_service.py` — `execute()` 入参新增 `channel_id`、`channel_capabilities` 可选参数
- `src/hecate/plugin/registry.py` — 注册新通道 adapter
- `src/hecate/plugin/spi/__init__.py` — 扩展 `ChannelABC` 文档(可选)
- `pyproject.toml` — 新增依赖 `lark-oapi`、`slack-bolt`、`aiohttp`,放 `[tools]` extras 而非默认依赖

**新增数据库迁移**:
- Alembic 迁移:`im_identity_bindings` 新表;`conversations`、`messages`、`sessions` 加字段迁移

**新增测试**:
- `tests/test_channel/test_im_adapters.py`(FeishuChannel/SlackChannel 单元测试)
- `tests/test_channel/test_im_binding.py`(绑定 token 流程测试)
- `tests/test_channel/test_message_bus.py`(异步解耦测试)
- `tests/test_api/test_channels_webhook.py`(webhook endpoint 集成测试)
- `tests/test_gateway/test_im_routing.py`(身份绑定 → 路由集成测试)

**对外 API 变化**(潜在 breaking):
- `WorkflowExecutionService.execute()` 新增可选参数,旧调用代码兼容
- `SessionRouter` 接口可能重命名(私有,影响内部)

**依赖变化**:
- 新增可选依赖 `lark-oapi[aiohttp]>=1.4.0`、`slack-bolt>=1.18.0`,放到 `[tools]` extras
- `uv pip install "hecate[tools]"` 启用 IM 通道能力

**配置变化**:
- 新增 `.env` 配置项:`HECATE_IM_FEISHU_APP_ID`、`HECATE_IM_FEISHU_APP_SECRET`、`HECATE_IM_FEISHU_VERIFICATION_TOKEN`、`HECATE_IM_FEISHU_ENCRYPT_KEY`、`HECATE_IM_SLACK_BOT_TOKEN`、`HECATE_IM_SLACK_SIGNING_SECRET`、`HECATE_IM_SLACK_APP_TOKEN`(Socket Mode)
- 现有 OpenAI 兼容 API 路径(`/v1/chat/completions`)保持不变,新增 webhook 入口并存