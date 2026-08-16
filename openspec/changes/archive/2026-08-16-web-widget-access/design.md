## Context

11.2 Web Widget (Simplified) 是 Multi-Channel Wave 1 的收尾项。Wave 1 中 11.3 Feishu 和 11.9 Slack 已交付,均走 `ChannelABC` 路径(webhook 入站 → `CanonicalMessage` 归一化 → `WorkflowExecutionService.execute()` → `ChannelABC.stream()` 回传)。Web Widget 不属于这个抽象:浏览器本身就是客户端,可以直接调 `/v1/chat/completions` SSE,跟 dashboard chat 走完全相同的代码路径。

当前 dashboard chat 的实现 `web/src/app/(dashboard)/chat/[conversationId]/page.tsx` 已经是完整可用的 chat UI —— 消息列表、流式渲染、kb / memory 标签、Queued 指示、New Chat 按钮。本次变更的核心动作是把这个页面拆成共享组件 `<ChatSurface>`,再加一个嵌入模式的路由壳子 + 气泡外壳。

约束:

- 后端零改动(`/v1/chat/completions`、`/api/conversations` 已有逻辑覆盖所有需求)
- 不引入 ChannelABC adapter、不引入新的 webhook endpoint、不引入 Alembic 迁移
- 必须能在 `<iframe>` 里渲染(资产相对路径 / 同源策略合规)
- 必须复用现有员工 JWT 鉴权,失败时按现有逻辑跳 `/login`

## Goals / Non-Goals

**Goals:**

- 提供一个独立的嵌入入口 `/embed/chat?agent=<uuid>`,在客户内部门户或 Hecate 自家 portal 里以 iframe + 气泡形式提供 chat 能力
- 把现有 dashboard chat 的逻辑抽成共享组件,消除两份实现带来的 drift 风险
- 用 ADR-031 把"Widget 不走 ChannelABC"的架构决策固化下来

**Non-Goals:**

- 公开匿名 to-C 场景、临时 JWT、RS256、Origin 白名单、WidgetModel(对应 11.2 完整版 / P5 deferred)
- `<script src="...">` loader.js 注入、CSS Shadow DOM 隔离
- Referer / 父源白名单校验
- i18n / 多语言(本次全英文)
- 浮动气泡的 localStorage 持久化、"今日不再显示"
- 紧凑的"重新登录"提示 UI(接受 iframe 内跳 `/login` 的体验)
- 后端 API 扩展、新增依赖、Alembic 迁移

## Decisions

### D1:浏览器直接调 `/v1/chat/completions`,**不**走 ChannelABC

Widget 的客户端是浏览器,浏览器天然可以直接 POST 到 `/v1/chat/completions` 并消费 SSE 流。ChannelABC 是为"平台中介的异步消息"抽象的 —— IM 平台拥有对话线程,我们通过 webhook 接收事件、解析 `CanonicalMessage`、调用 Agent、回传响应。这种抽象在 widget 场景下不适用:浏览器 → 我们的后端是同步的、可流式的、没有第三方中介。

替代方案:把 widget 也实现成 ChannelABC adapter(类似 Feishu / Slack)。代价是要给 widget 自定义一个"webhook"(其实是 iframe 里的 fetch),绕过 IM 平台的所有概念(签名、token、卡片、富文本)—— 抽象不匹配,徒增复杂度。

参考开源:Intercom widget、Dify embed、Salesforce Enhanced Web Chat、Dialogflow Messenger 都不是平台中介模型,都是浏览器直连。

后果:

- 简化版的 widget **不**注册到 `PluginRegistry`,`POST /v1/channels/web-widget/webhook` 永远 404
- 后续 11.2 完整版如果要把 widget 接入 ChannelABC,需要新的架构决策(很可能不复用本次的 UI 代码)
- ADR-031 显式记录这个决定

### D2:抽出共享组件 `<ChatSurface>`,而不是复制 dashboard chat 代码

`(dashboard)/chat/[conversationId]/page.tsx` 现在包含完整的 chat UI 逻辑。如果直接复制一份到 `/embed/chat`,会有:

- dashboard chat 加新功能(widget 不跟进 → drift)
- widget 修 bug(dashboard 不跟进 → drift)
- 测试要么写两份、要么只覆盖一份

抽出 `<ChatSurface>` 组件接受 `mode: "dashboard" | "embed"` prop(只控制 chrome:sidebar / 顶部 header),核心渲染逻辑只有一份。

替代方案 A(共享组件 + 共享 header)被采纳。替代方案 B(完全复制 + 同步脚本)被否,drift 风险不可控。

### D3:iframe 模式 + 同 Next.js 工程,不做独立 loader.js bundle

Widget 用 `<iframe src="/embed/chat?agent=<uuid>">` 嵌入到宿主页面。同 Next.js 工程里加一条路由 `/embed/chat`,沿用现有的 Tailwind / 组件库 / AuthGuard / `api-client`。

iframe 模式天然解决样式隔离问题:iframe 内部的 CSS / 全局样式 / Tailwind preflight 不会泄漏到宿主页面;反过来宿主页面的样式也不会影响 iframe 内容。

替代方案:独立 loader.js bundle(`<script src="https://hecate.example/embed.js">` 自动注入气泡 + iframe)。代价是要解决样式隔离(Shadow DOM 或 CSS Modules scope)、bundle 打包、跨域 cookie / localStorage 共享、HMR 等问题。对 S 工作量来说不划算,而且这个模式对应的是"公开匿名 to-C"场景(任意第三方网站嵌入),归 11.2 完整版。

后果:

- 嵌入方必须能在自己的 HTML 里加一个 `<iframe>`,不能纯 `<script>`(差别不大,但要文档说明)
- 浏览器跨域 cookie / localStorage 在 iframe 内是按 iframe 的 origin 隔离的,跟宿主 origin 无关 —— 这正好是我们想要的(每个嵌入站点各自一份 Hecate session)

### D4:首次加载自动创建 conversation

embed 路由 mount 时,如 `?agent=` 合法,自动调 `POST /api/conversations { agent_id }` 拿到 `conversationId`,后续所有 `/v1/chat/completions` 调用都传 `session_id=conversationId`。

替代方案:要求嵌入方在 URL 里传 `conversationId`。代价是嵌入方要么自己调 `POST /api/conversations`(暴露后端 API 给门户代码),要么用户必须先在 dashboard 里建好会话再切到 widget(体验割裂)。S 工作量下自动创建更简单,跟 dashboard 行为一致。

后果:

- 刷新 widget = 新建会话(dashboard chat 同样的行为)
- "New Chat" 按钮 = 在 embed 内手动建新会话(也是 dashboard 行为)
- 这两个行为对 to-B 内部场景可接受

### D5:气泡外壳接受最小可用 UX,不做持久化

`<WidgetBubble>` 提供:右下角固定气泡、点击展开、关闭按钮(×)收起、刷新页面回到收起状态。**不**做:

- localStorage 持久化展开 / 收起状态(关闭后刷新页面重新出现,符合"最小可用"原则)
- "今日不再显示" 按钮(超出 S 范围)
- 嵌入式主题切换(暗色模式等归后续)
- 多 tab 联动(同一 host 页面多个 widget 实例各自独立)

后果:

- 实现成本低(S 工作量覆盖得了)
- 副作用:用户关闭后必须刷新页面才能再次展开 —— 内部 portal 用户可接受;to-C 场景会不满,留作完整版跟进

### D6:复用 AuthGuard + api-client 401 拦截,iframe 内跳转 `/login`

embed 路由跟 dashboard 路由一样挂 `<AuthGuard>`。未登录用户跳 `/login`(iframe 内跳转)。

如果用户已登录但 access_token 在 widget 使用过程中过期,下一次 `/v1/chat/completions` 或 `/api/conversations` 拿到 `401`,现有 `api-client` 的 401 拦截器会清掉 `localStorage` 里的 token 并跳 `/login`。

接受 iframe 内跳转到 `/login` 的体验(用户在客户门户里打开 widget,token 过期 → iframe 突然变登录页)。如果后续要优化成"iframe 内紧凑提示",由 11.2 完整版跟进。

替代方案:用 `window.postMessage` 让宿主页面统一处理登录态。代价是 iframe 和宿主页面要约定通信协议,引入新的耦合面。S 工作量下不值得。

### D7:范围澄清段(关于 11.2 简化版 vs 完整版)

`docs/features/feature-catalog.md` 第 382 行把 11.2 描述为"内部 Portal / embeddable for any Hecate deployment",措辞偏简略,容易跟公开匿名 to-C 场景混淆。

在本次变更的 deliverable 里(以及未来 ADR-031),明确:

- **本次简化版覆盖**:Hecate 自家 portal + 客户内部门户,用户是 Hecate 员工(已有 JWT),iframe 嵌入,浏览器直连 `/v1/chat/completions`
- **本次简化版不覆盖**:任意第三方公开网站 `<script>` 嵌入、匿名 to-C 用户、临时 JWT、Origin 白名单 —— 那是 P5 deferred 的 11.2 完整版,单独立项

澄清段落在 ADR-031 的 Context 一节以及 tasks.md 的 README 注释里。不直接改 catalog.md(那是事实索引,改它要连带改 roadmap、p3-mvp-audit、多个 ADR 引用,本次没必要)。

## Risks / Trade-offs

**[Risk] Drift between `<ChatSurface>` 和 dashboard chat 的边角行为** → Mitigation:抽出时跑一遍 dashboard chat 的端到端用例(创建会话、发送、流式、New Chat、Queued 状态、kb / memory 标签)做回归;embed 页面用同一份组件,行为自动同步。

**[Risk] iframe 模式下 AuthGuard 跳转 `/login` 体验割裂** → 接受:简化版范围内就这样;完整版跟进紧凑提示 UI。

**[Risk] 自动创建 conversation 导致每次刷新都产生新会话,污染对话历史** → 接受:dashboard chat 行为一致;to-B 内部场景下用户可识别这是 widget 自动建的会话;后续如果用户反馈强烈,在 dashboard 加 filter("Hide widget sessions")。

**[Risk] `<iframe>` 嵌入需要嵌入方自己写 HTML,不是纯 `<script>` 集成** → Mitigation:在交付时写一段 5 行的 HTML snippet 文档说明,作为 README 的一部分附在 ADR-031 后面。

**[Risk] Widget 不接 ChannelABC,后续完整版要重构** → 这是有意为之的:简化版和完整版是两种架构形态,简化版的目标用户(Hecate 员工)不需要 channel 抽象。完整版会带自己的架构决策(很可能不复用本次的 UI 代码),不是 incremental 演进。

**[Risk] Tailwind preflight 在 iframe 内被应用,但宿主页面的 React / Vue 框架可能用 MutationObserver 监控 DOM 变化,iframe 内部的 micro-task / animation 可能被宿主视为异常** → Mitigation:气泡用 CSS `transform` / `transition` 而非 JS 动画;关闭时直接 unmount,不做 fade-out。

## Migration Plan

无数据迁移、无后端 schema 变更、无 API 版本切换。

部署步骤:

1. 合并 PR 到 `main`
2. 重新部署 Hecate 控制台(Next.js build)
3. 在 Hecate 自家 portal(若有)加入 `<iframe src="https://<host>/embed/chat?agent=<uuid>">` 演示
4. 通知有意向的客户内部门户试用

回滚:仅前端路由 + 组件,可随时 revert commit,无需清理数据库或重置任何服务。

## Open Questions

无。`?agent=` query 参数单一入口、agent 必填、刷新即建新会话、关闭按钮刷新重出、英文优先 —— 所有决定都已固化在 proposal 和 spec 里,implement 阶段不需要再拍板。
