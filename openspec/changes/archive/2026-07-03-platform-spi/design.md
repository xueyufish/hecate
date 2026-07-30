## Context — 背景

Hecate 是一个企业级 Agent 平台，拥有 15 个扩展点。最近完成了 Plugin SPI Core (5.5a)，建立了 SPI 扩展点的模式：EvaluatorABC → BuiltinEvaluator → PluginRegistry。

本次变更将该模式扩展到四个新的 SPI 领域：外部平台适配器（ChannelABC）、认证提供者（AuthProviderABC）、国际化（i18n SPI），并将现有通知系统合并到 Channel 模型中。

当前代码库的现状：
- REST API 是唯一的 Agent 交互通道（`api/v1/chat.py`，454 行）
- 认证流程在 `core/deps_workspace.py` 中为单体架构（JWT → API Key → Env Key）
- 通知分发在 `services/notification_dispatcher.py` 中硬编码（基于 ChannelType 的 switch/case）
- 零 i18n 基础设施

行业分析（OpenClaw、AgentScope、Salesforce AgentForce、Amazon Bedrock AgentCore、ch4p、CAR）确认 Gateway + Channel Adapter 模式是标准方法。

## Goals / Non-Goals — 目标 / 非目标

**目标：**
- 将 ChannelABC 定义为外部平台适配器的抽象接口（非 REST/WS/CLI 传输层）
- 引入 Gateway 层用于会话路由和消息标准化
- 定义 AuthProviderABC 用于可插拔认证（与 EvaluatorABC 相同模式）
- 定义完整的 i18n SPI 范围：语言检测、消息目录、`t()` 函数、插件翻译、管理 API、运行时切换
- 将 NotificationDispatcher 合并到 Channel 模型（统一 `type="channel"`）
- 对现有代码零破坏性变更——所有变更都是新增式的

**非目标：**
- 实现具体的平台通道（飞书、Slack、Discord）——那是 Sprint 6+ 的内容
- 将现有 REST API 迁移到 Gateway——渐进式迁移，非大爆炸式
- SSO/LDAP/OAuth2 AuthProvider 实现——仅限 ABC 和内置的 JWT/APIKey
- 性能优化——正确性优先，优化在后

## Decisions — 决策

### D1: ChannelABC 是外部平台适配器，而非传输抽象

**决策：** ChannelABC 抽象的是"如何与特定平台通信"（飞书 Bot API、Slack Bolt SDK、Telegram grammY），而非"如何传输消息"（HTTP、WebSocket、stdin/stdout）。

**理由：** 行业共识（OpenClaw 的 ChannelPlugin、AgentScope 的 Channel Adapter、ch4p 的 IChannel、CAR 的 ChannelAdapter）都将通道定义为具有规范消息格式的平台特定适配器。REST/WS/CLI 是 Gateway 传输层——它们处理线路协议，而非平台语义。

**备选方案：** 将 ChannelABC 定义为 REST/WS/CLI 抽象（原始路线图）。被拒绝因为 REST、WebSocket 和 CLI 在架构上差异太大（同步 vs 异步、无状态 vs 有状态、基于进程 vs 持久连接），无法共享有意义的 ABC。

### D2: Gateway 是新增层，而非替代品

**决策：** Gateway 位于通道和 WorkflowExecutionService 之间。它从通道接收 CanonicalMessage，解析会话上下文，并委托给现有的服务层。

```
Channel.receive(raw) → CanonicalMessage → Gateway.route() → WorkflowExecutionService → PregelRuntime
```

**理由：** Salesforce 的"一次构建，随处部署"模式。Agent 逻辑（WorkflowExecutionService、PregelRuntime）完全与通道无关。Gateway 增加了会话路由而不触及业务逻辑。

**备选方案：** 通道直接调用 WorkflowExecutionService（无 Gateway）。被拒绝因为会在每个通道中重复会话路由逻辑。

### D3: CanonicalMessage 是通用消息格式

**决策：** 定义一个冻结数据类 `CanonicalMessage`，包含字段：`id`、`channel_id`、`user_id`、`session_id`、`content`（文本 + 附件）、`metadata`（平台特定透传）、`timestamp`。

**理由：** 每个行业实现（OpenClaw、AgentScope、ch4p、CAR）都在边缘将平台特定消息标准化为规范格式。Agent 大脑只看到 CanonicalMessage。

### D4: ChannelCapabilities 使用声明式模型

**决策：** 每个 Channel 通过冻结数据类声明其能力：

```python
@dataclasses.dataclass(frozen=True)
class ChannelCapabilities:
    streaming: bool = False
    interactive_buttons: bool = False
    file_upload: bool = False
    markdown: bool = False
    rich_cards: bool = False
    max_message_length: int | None = None
```

**理由：** OpenClaw 的 ISP 方法——能力是声明的，而非分支的。Gateway 在尝试操作前检查能力。如果 `streaming=False`，Gateway 发送缓冲响应。

### D5: NotifierABC 合并到 ChannelABC（统一 type="channel"）

**决策：** 通知分发器（Email、飞书卡片、Slack Block Kit、Webhook）成为 Channel 适配器。PluginRegistry 对所有这些使用 `type="channel"`。

**理由：** 通知是一种向平台发送出站通信的形式——与 Channel 的 `respond()` 方法在结构上相同。将两者分开会增加复杂性而没有益处。

**备选方案：** 保持 NotifierABC 独立，使用 `type="notifier"`。被拒绝因为区分是人为的——两者都是"向平台发送消息"。

### D6: AuthProviderABC 完全遵循 EvaluatorABC 模式

**决策：**
```python
class AuthProviderABC(ABC):
    @property
    @abstractmethod
    def name(self) -> str: ...
    
    @property
    @abstractmethod
    def description(self) -> str: ...
    
    @abstractmethod
    async def authenticate(self, token: str, db: AsyncSession) -> AuthContext | None: ...
```

内置：`JWTAuthProvider`、`APIKeyAuthProvider`。现有的 `get_auth_context()` 遍历已注册的提供者，第一个非 None 结果胜出。

**理由：** 在 EvaluatorABC 中已验证的模式。authenticate() 在失败时返回 `None`（而非异常），允许尝试下一个提供者。

### D7: i18n SPI 使用 JSON/YAML 消息目录

**决策：** 翻译文件使用 JSON 格式（主要），支持 YAML。文件结构：`locales/{lang}/{namespace}.json`。`t()` 函数支持嵌套键、复数化和参数插值。

**理由：** JSON 被普遍支持，易于验证，且人类可读。YAML 作为次要选项给偏好它的团队。gettext 对于 Python 优先的平台来说过于复杂。

**备选方案：** gettext (.po/.mo)。被拒绝因为它需要编译步骤，且与 JSON 相比在 Python 生态中的工具支持较差。

### D8: i18n 管理 API 用于翻译文件上传

**决策：** `/api/i18n/` 下的 REST 端点：
- `POST /api/i18n/translations` — 上传翻译文件（JSON/YAML）
- `GET /api/i18n/translations/{locale}` — 下载当前翻译
- `GET /api/i18n/locales` — 列出可用语言
- `PUT /api/i18n/translations/{locale}/{namespace}` — 更新特定命名空间

**理由：** 企业需求——团队需要通过 API 管理翻译，而不仅是文件。

### D9: 通过 PluginManifest 注册插件翻译

**决策：** 插件在 PluginManifest 中声明其翻译命名空间。当插件注册时，其翻译自动加载。

```python
@dataclasses.dataclass(frozen=True)
class PluginManifest:
    # ... 现有字段 ...
    translations: tuple[str, ...] = ()  # 例如 ("plugin-name:common", "plugin-name:errors")
```

**理由：** 插件需要自己的翻译。在清单中声明可确保插件注册时加载，无需单独配置。

## Risks / Trade-offs — 风险 / 权衡

**[风险] Gateway 成为瓶颈** → 缓解措施：Gateway 是无状态且可水平扩展的。会话路由是简单的查找，不是重型计算。

**[风险] ChannelCapabilities 无限增长** → 缓解措施：能力是按通道声明的，而非全局的。新能力以 `False` 默认值添加到数据类——现有通道不会中断。

**[风险] AuthProvider 迭代顺序重要** → 缓解措施：提供者使用显式顺序注册。第一个非 None 结果胜出，这是标准模式（与 `get_auth_context()` 已经使用 JWT → APIKey → EnvKey 的方式相同）。

**[风险] i18n 翻译文件冲突** → 缓解措施：命名空间键（例如 `plugin-name:key`）。后注册的覆盖同一命名空间的早期注册。管理 API 显示每个键属于哪个插件。

**[风险] 将现有 REST API 迁移到 Gateway** → 缓解措施：渐进式迁移。现有的 `api/v1/chat.py` 继续直接与 WorkflowExecutionService 工作。添加一个包装相同逻辑的 `RESTChannelAdapter`。Gateway 通过它路由。无大爆炸迁移。

## Open Questions — 开放问题

1. Gateway 应该是独立进程（如 ch4p）还是进程内（如 AgentScope）？
   - 当前思路：为简单起见使用进程内，以后可以选择提取出来。

2. CanonicalMessage 是否应包含对话历史还是仅当前消息？
   - 当前思路：仅当前消息。历史由会话/上下文系统管理，而非通道。
