## Why — 为什么

Hecate 的 Agent 交互目前硬编码为 REST API（`api/v1/chat.py`）。添加新平台（飞书、Slack、Discord、Telegram）需要修改核心代码。通知系统（`NotificationDispatcher`）使用基于通道类型的 switch/case，同样僵化。认证是一个单体 `get_auth_context()` 函数，不编辑就无法扩展。而且零 i18n 基础设施。

本次变更引入了 Platform SPI 层——一个基于插件的架构，用于外部平台适配器、认证提供者和国际化——遵循 Salesforce 的"一次构建，随处部署"模式和 EvaluatorABC 先例。

## What Changes — 变更内容

- **ChannelABC**（新 SPI）：外部平台适配器的抽象接口。每个 Channel 将特定平台（飞书、Slack、Telegram、Email、Webhook）适配为规范消息格式。现有的 REST API、CLI 和 NotificationDispatcher 成为 Channel 实现。
- **Gateway**（新层）：位于通道和 Agent 运行时之间。处理会话路由、消息标准化，并委托给 WorkflowExecutionService。REST API 路由保留——Gateway 是新增的，而非替代品。
- **AuthProviderABC**（新 SPI）：认证提供者的抽象接口。内置：JWTAuthProvider、APIKeyAuthProvider。现有的 `get_auth_context()` 遍历已注册的提供者。未来：SAML、LDAP、OAuth2。
- **i18n SPI**（新增）：语言检测、消息目录加载、`t()` 翻译函数、插件翻译注册、翻译文件管理 API、运行时语言切换。
- **NotifierABC 合并到 ChannelABC**：通知分发器（Email、飞书卡片、Slack Block Kit）成为 Channel 适配器。PluginRegistry 使用统一的 `type="channel"`。

## Capabilities — 能力

### 新能力

- `channel-adapter`：用于外部平台适配器的 ChannelABC 接口、用于会话路由和消息标准化的 Gateway 层、CanonicalMessage 格式、ChannelCapabilities 声明
- `auth-provider`：用于可插拔认证的 AuthProviderABC 接口、内置 JWT 和 APIKey 提供者、认证流程中的提供者注册和迭代
- `i18n-spi`：语言检测（请求头 / 用户偏好 / 工作区设置）、MessageCatalog 加载（JSON/YAML）、`t()` 翻译函数、插件翻译注册、翻译文件上传的管理 API、运行时语言切换

### 修改的能力

- `builtin-evaluators`：需求无变化——仅作为模式一致性的实现参考

## Impact — 影响

**零破坏性变更。** 所有现有代码继续不变地工作：

| 组件 | 影响 |
|-----------|--------|
| PregelRuntime | 无——引擎内部不受影响 |
| WorkflowExecutionService | 无——业务逻辑层不受影响 |
| 认证系统 | 无——`get_auth_context()` 保留，提供者并行添加 |
| 管理 API | 无——这些是管理端点，非 Agent 交互通道 |
| REST API（`api/v1/chat.py`） | 渐进式迁移——成为 Channel Adapter，但 API 契约保留 |
| CLI（`cli/client.py`） | 渐进式迁移——成为 Channel Adapter |
| `notification_dispatcher.py` | 渐进式迁移——成为出站 Channel Adapter 模式 |

**新代码位置：**
- `src/hecate/gateway/` — Gateway、CanonicalMessage、会话路由
- `src/hecate/channel/` — ChannelAdapter ABC、内置适配器
- `src/hecate/auth/` — AuthProviderABC、内置提供者
- `src/hecate/i18n/` — LocaleResolver、MessageCatalog、`t()` 函数

**依赖：** Plugin SPI Core (5.5a)——已完成并合并。
