## Context

**当前状态**(2026 年 8 月):

Hecate 已经在 engine 层和 channel 层定义了三组相关但完全不同的"channel"概念:

```
概念 A:engine/channel.py
  → Pregel 运行时数据通道(LAST_VALUE / TOPIC / ACCUMULATOR)
  → 已通过 2026-06-06-channel-registry 重构为 ChannelBehavior + ChannelTypeRegistry
  → 本次变更不涉及

概念 B:src/hecate/channel/
  → 外部 IM 平台适配器(飞书/Slack/Telegram/Email)
  → ChannelABC、CanonicalMessage、ChannelCapabilities 抽象已定义
  → register_channels() 的 channel_classes list 为空
  → Gateway.route() 末尾写着 "TODO: Delegate to WorkflowExecutionService when integrating"
  → SessionRouter 是纯内存 dict,进程重启就丢失
  → 现有三个实现 NotificationChannelAdapter 子类是出站告警专用,本次不动
  → 本次变更的核心目标

概念 C:用户会话(ConversationModel/SessionModel/MessageModel)
  → 已有完整持久化模型,workspace_id 多租户隔离已就位
  → 但完全无 "source_channel" 字段——不知道消息从哪个通道来
  → 本次变更要扩展这些模型
```

**约束**:

- 多租户:Hecate 是 `Organization → Workspace → RBAC` 的多租户架构,IM 凭证和绑定关系必须按 `workspace_id` 严格隔离
- 已有的 OpenAI 兼容 API(`POST /v1/chat/completions`)必须维持不变,IM webhook 入口是新增,不是替换
- 官方 SDK 优先:飞书 `lark_oapi.channel` 和 Slack `slack_bolt` 已经把签名/重连/去重/限流处理完,我们只做薄层包装
- 已有 `vault/provider.py` 提供 `SecretProviderABC` 抽象,IM 凭证加密存储应复用而非新建
- 已有 `engine/eventbus.py` 提供事件总线,MessageBus 优先基于 EventBus 实现,不新建并行总线

**相关方**:

- 终端用户(workspace 成员):通过飞书/Slack 跟 Hecate Agent 对话
- 租户管理员:配置 workspace 的 IM App 凭证,管理 IM 绑定关系
- 平台运营方:维护 SDK 依赖、监控消息吞吐、处理 webhook 异常

## Goals / Non-Goals

**Goals**:

- 在 Hecate 现有 `ChannelABC` 抽象之上,实现飞书(Socket Mode / Webhook 双 transport)和 Slack(Socket Mode / Webhook)两个生产级入站通道
- 新增 `POST /v1/channels/{name}/webhook` 入口,完成 raw payload → CanonicalMessage 的归一化
- 新增 IM 身份绑定数据模型和 Web UI 引导流程,实现 "Bound Identity" 强制绑定
- 修改 `Gateway.route()`,接入 `WorkflowExecutionService.execute()`,完成入站消息到 Agent 执行的端到端联通
- 新增 MessageBus 解耦 webhook 立即 ACK(3 秒内)与 Agent 异步执行(可能数十秒),不破坏飞书/Slack 的 webhook 超时约束
- 同一 Hecate user 在飞书和 Slack 上的会话历史合并(共享 conversation)
- 数据模型加 `source_channel` 字段,保留审计能力

**Non-Goals**(本次明确不做):

- 不实现钉钉、企业微信、Telegram、Discord、Matrix 等其他 IM 平台
- 不重写或修改 `NotificationChannelAdapter`(出站告警)及其三个实现类
- 不做 "Channel as Tool"(把 IM 封装成 MCP server 让 Agent 主动调用)
- 不做 SCIM 自动同步(后续 Phase),先用 Web UI 手绑
- 不做 Phase 2+ 的流式卡片(飞书 CardKit updating / Slack chat.update),MVP 只做基础文本流式
- 不做 "ambient room events"(群聊未 @ 内容作为上下文),MVP 只处理显式 @ 触发的消息
- 不做 "bot loop protection"(防双 bot 互相回复)
- 不做 IM 消息到现有 OpenAI 兼容 API 的反向代理(那是两个不同的入口,各自独立)

## Decisions

### D1:ChannelABC 作为主抽象,不重新设计

**选择**:复用现有 `src/hecate/channel/adapter.py` 的 `ChannelABC`,不引入新的"IMChannel"或"ExternalChannel"子类。

**考虑的替代方案**:
- 新建 `IMChannelABC` 继承 `ChannelABC`,加 `bind_user(im_user_id, workspace_id)` 等 IM 特有方法 → 引入新抽象层级,Phase 2/3 加钉钉时会更复杂
- 完全重写一个 "HecateIMChannel" 抽象 → 推翻现有骨架

**理由**:现有 `ChannelABC` 已经具备 `receive/respond/stream + capabilities` 的完整双向契约。`ChannelCapabilities` 已经声明 `streaming/interactive_buttons/rich_cards` 等富消息能力。IM 特有逻辑(身份绑定、凭证管理)放在 adapter 内部,不污染抽象层。deer-flow 和 AgentScope 都用单一抽象。

### D2:基于官方 SDK 做薄层包装,不重新实现 SDK

**选择**:
- FeishuChannel 包装 `lark_oapi.channel.FeishuChannel`(用 `transport="ws"` 优先,`webhook` fallback)
- SlackChannel 包装 `slack_bolt.App`(用 `SocketModeHandler` 优先,HTTP Request URL fallback)

**考虑的替代方案**:
- 自己用 httpx 直接调飞书/Slack OpenAPI → 重新实现签名验证、重连逻辑、去重、限流,工作量大且维护成本高
- 用社区第三方 SDK(非官方) → 安全风险和更新延迟

**理由**:deer-flow、AgentScope、OpenClaw、Hermes Agent 全部走官方 SDK。SDK 已经把 transport/签名/重连/去重/卡片更新节流做完,薄层包装只负责 "归一化为 CanonicalMessage + 调 WorkflowExecutionService"。

### D3:强制 Bound Identity,token 模式

**选择**:IM 用户首次发消息时被拒绝,bot 返回一次性绑定 token 链接,用户必须在 Web UI 完成 SSO 登录并确认绑定才能继续对话。

**考虑的替代方案**:
- 可选绑定(shadow user 模式)→ 多租户归属问题、账户爆炸、合规审计弱,跟企业级定位不符
- Operator approval(OpenClaw 模式)→ 多租户下不可行,管理员不可能去 CLI 批准
- 预绑定(SCIM 自动同步)→ 实施成本高,留 Phase 2/3

**理由**:强制绑定在企业内部场景 friction 可接受(员工登录一次而已),身份治理最干净,跟 Hecate 现有 RBAC 模型契合。后续 Phase 2 加 SCIM 预绑定实现"零摩擦强制绑定"。

### D4:跨通道会话历史共享(同一 Hecate user 合并)

**选择**:同一个 Hecate user 绑定了飞书张三和 Slack 张三后,两个通道的消息路由到同一个 conversation。

**确定性映射**(参考 deer-flow):
```
channel_type="feishu" + im_app_id + im_chat_id + user_id
       ↓ SHA-256
       → UUID → conversation_id(主键)
```

**考虑的替代方案**:
- 每通道独立会话 → 用户切换通道上下文丢失,客服场景体验差
- Agent 级别共享(同一 Agent 的所有用户合并) → 跨用户泄漏,不可接受

**理由**:deer-flow、ChatGPT App、Claude.ai 都走这条路,企业级客服/HR 场景的标配。绑定机制(Bound Identity)已解决身份对齐问题,workspace 隔离确保跨租户安全。

### D5:MessageBus 解耦 webhook ACK 与 Agent 执行

**选择**:webhook 入口在收到消息后立即(200ms 内)返回 200 OK,同时把消息投递到内存 MessageBus,后台 asyncio task 消费执行。

**考虑的替代方案**:
- 同步等待,3 秒后返回 placeholder → 飞书/Slack 都不允许超时,失败率高
- 同步等待,等 Agent 跑完再返回 → 长任务必然超时
- 用现有 Temporal workflow → 引入额外基础设施成本,对简单场景过度

**理由**:飞书要求 3 秒内 ACK,Slack 推荐 3 秒内 ACK,Agent 跑一次可能数十秒。deer-flow 的 MessageBus 模式经实践验证。优先基于现有 `engine/eventbus.py` 的 EventBus 抽象实现,不新建并行基础设施。

### D6:数据模型增量,不改 OpenAI 兼容 API 路径

**选择**:`ConversationModel/SessionModel/MessageModel` 各新增 `source_channel` 字段(全部 nullable,默认 `None` 表示 OpenAI 兼容 API 来源),`ConversationModel` 新增 `im_chat_id`(nullable)。新增独立表 `IMIdentityBindingModel`。

**考虑的替代方案**:
- 新建独立 `IMConversation` 表与 `ConversationModel` 并行 → 双表 join 复杂,数据模型分裂
- 复用现有 `metadata_` JSON 字段塞 channel 信息 → 失去类型安全和索引能力

**理由**:数据模型层面小改动,业务代码通过 `source_channel` 字段做分支(API 路径走原流程,IM 路径走新流程)。`im_chat_id` 是回复路由必需(飞书需要 `chat_id` 调 send API)。

### D7:Webhook endpoint 路径与现有 API 并列

**选择**:新增 `POST /v1/channels/{name}/webhook`,与现有 `POST /v1/chat/completions` 并列。

**考虑的替代方案**:
- 替换 `/v1/chat/completions` 为 webhook 入口 → BREAKING 变更,所有现有客户端失效
- 复用 `/v1/chat/completions` 接收 IM 消息 → 语义混乱,参数差异大

**理由**:两个入口职责清晰分离。`{name}` 是 ChannelAdapter 名,通过 PluginRegistry 动态解析。飞书飞书开发平台和 Slack App 配置的 Request URL 指向 `/v1/channels/feishu/webhook` 和 `/v1/channels/slack/webhook`。

### D8:凭证加密复用 `vault/provider.py`

**选择**:飞书 `app_secret`、Slack `signing_secret` 等存到现有 `SecretProviderABC` 抽象的具体实现(默认本地加密存储,生产可对接 HashiCorp Vault)。

**考虑的替代方案**:
- 新建 IM 凭证专用的加密层 → 重复建设
- 直接存数据库明文 → 安全风险

**理由**:Hecate 已有 `SecretProviderABC` 抽象,定义 `get_secret(path)` 接口。IM 凭证以路径约定存储(如 `secret/hecate/im/feishu/app_secret`),跟其他密钥统一管理。

### D9:依赖放到 `[tools]` extras

**选择**:`pyproject.toml` 新增可选依赖组 `[tools]` 包含 `lark-oapi[aiohttp]>=1.4.0` 和 `slack-bolt>=1.18.0`,默认 `pip install hecate` 不安装。

**考虑的替代方案**:
- 放默认依赖 → 没用到 IM 的用户也要装 SDK,违反 YAGNI
- 放 `[im]` extras → 跟项目已有的 `[tools]` 分组不一致,且新增分组带来不必要的复杂度

**理由**:Hecate 现有分组 `[llm]`/`[temporal]`/`[rag]`/`[security]`/`[tools]`/`[observability]`/`[mysql]`/`[scheduling]`/`[dev]` 都是"能力集合"维度,IM SDK 属于工具能力,跟 `[tools]` 同维度。`uv pip install "hecate[tools]"` 启用。

## Risks / Trade-offs

| Risk | Mitigation |
|------|------------|
| **Webhook 3 秒超时**:飞书/Slack webhook 期望 3 秒内 ACK,Agent 执行可能数十秒 | MessageBus 立即 ACK + 后台执行;Phase 2 加流式卡片时进一步缩短感知延迟 |
| **多租户 IM 凭证共享风险**:同一飞书 App 服务多个 workspace 会出现路由混乱 | 强制每个 (workspace, channel) 组合用独立 IM App 凭证,通过 `im_app_id` 区分 workspace |
| **IM 用户身份冒用**:恶意用户伪造 webhook payload 模拟另一个用户的 open_id | webhook 签名验证(飞书 encrypt_key、Slack signing_secret)强制开启;IM Identity 不是凭据,不参与签名,服务端用 IM 平台返回的真实 `sender.open_id` |
| **数据库迁移风险**:给 conversations/messages/sessions 加字段涉及大量存量数据 | 所有新增字段 nullable,迁移不破坏存量数据;Alembic 迁移脚本加默认值测试 |
| **lark_oapi / slack_bolt SDK 版本兼容性** | 锁定 minor 版本,CI 加 SDK 升级测试;Phase 1 用 Socket Mode 优先(Webhook 后续) |
| **SecretProviderABC 默认实现缺失**:现有 vault 是抽象,可能没本地实现 | 不依赖某个具体 SecretProvider,默认实现通过现有方式注入,测试用 fixture |
| **ChannelABC.stream() 抽象已有但未实现,调用方怎么写** | Phase 1 FeishuChannel/SlackChannel.stream() 直接 raise NotImplementedError 或基础版本(单条消息发送),Phase 2 加流式卡片 |
| **OpenAI 兼容 API 用户的行为不能降级** | 所有 source_channel 新增字段 nullable,无 IM 配置的 workspace 仍走原路径 |
| **跨通道会话合并带来的隐私问题**:用户不希望 Slack 私聊出现在飞书历史 | 留 Phase 2 加用户级 "cross-channel sync" 开关,默认开启但可关闭 |
| **M3 实验性合规要求**:webhook payload 含敏感信息(IM 用户标识、消息内容) | 日志记录脱敏(只留 message_id,不存原始 content);ContentStoragePolicy 配置项保留 |

## Migration Plan

**前置条件**:
- 飞书/Lark 开发者账号已创建企业自建应用,获取 `app_id`/`app_secret`/`verification_token`/`encrypt_key`
- Slack App 已创建,获取 `bot_token`(xoxb-)/`signing_secret`,Socket Mode 启用并生成 `app_token`(xapp-)

**部署步骤**(Phase 1 灰度):

1. 部署代码(启用 `[tools]` extras),不启用 IM 通道环境变量
2. 管理员在 workspace 设置页配置 IM App 凭证(存入 SecretProvider)
3. 管理员在飞书/Lark 开发者后台配置 Request URL 为 `https://hecate.corp.com/v1/channels/feishu/webhook`
4. 管理员在 Slack App Event Subscriptions 配置 Request URL 为 `https://hecate.corp.com/v1/channels/slack/webhook`
5. 触发飞书/Slack 平台验证(Request URL 验证,平台向 URL 发 GET/POST challenge)
6. workspace 成员收到 IM 消息触发绑定流程,完成 SSO 登录确认
7. 单 workspace 灰度,验证一周后再扩 workspace

**回滚策略**:
- 数据库迁移:所有新增字段 nullable,迁移前可直接回滚 Alembic(无数据迁移需要)
- 新 endpoint 独立 FastAPI router,删除 `api/v1/channels.py` 即关闭 IM 入口
- 删除环境变量配置即停用 IM 通道,不影响 OpenAI 兼容 API

**Phase 2 升级路径**(非本次):
- MessageBus 持久化(基于 Redis 或现有 PostgreSQL event store)
- 飞书 CardKit 流式 / Slack chat.update streaming
- 钉钉/企业微信/Telegram 适配器
- "Channel as Tool" MCP server

## Open Questions

待 `tasks.md` 阶段或实施时确定,不影响本次 proposal 决策:

1. **FeishuChannel transport 默认值**:Socket Mode(无需公网 IP)vs Webhook(需要公网 URL)哪个默认?—— 倾向 Socket Mode,但需确认生产环境网络策略
2. **MessageBus 持久化策略**:MVP 用内存消息队列,Phase 2 是否需要 Redis/PostgreSQL?—— MVP 接受进程重启丢未执行消息的风险
3. **绑定 token 存储后端**:默认本地加密文件 vs 强制 SecretProvider?—— 倾向默认本地加密,SecretProvider 作为可选增强
4. **流式输出 Phase 1 形态**:完全同步阻塞等全部跑完再发一条 vs 分批更新(每次 1-2 秒)?—— 倾向 Phase 1 等全部跑完发一条完整消息,Phase 2 加流式
5. **Webhook 入口鉴权**:除了 SDK 签名验证外,是否需要 IP 白名单?—— 飞书/Slack 签名验证已足够,IP 白名单留运维配置项
6. **测试策略**:单元测试用 stub adapter,集成测试用 mock SDK 还是真实平台?—— 倾向单元 stub + 集成 mock,真实平台测试留 e2e 套件(需要 test bot)