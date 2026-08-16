## Why

Hecate 多渠道(Multi-Channel)Wave 1 已经交付了 11.3 Feishu 和 11.9 Slack 两个 IM 通道,但缺少 Web Widget —— 内部门户最常用的入口形态。catalog 把 11.2 Web Widget (Simplified) 定位为 S 工作量:复用现有 `(dashboard)/chat` 路由 + 员工 JWT 登录,补齐 Wave 1 收尾。

当前缺口:Hecate 自家 portal 和客户内部门户没法嵌入 Hecate 的 chat 能力。要让用户在门户里点开一个气泡就能跟 Hecate Agent 对话,目前必须跳到独立窗口。

为什么不复用 11.3/11.9 的 ChannelABC 路径:Web Widget 的客户端是浏览器本身,浏览器可以直接调 `/v1/chat/completions`(SSE 流式),而 ChannelABC 是为"平台中介异步消息"(IM 平台拥有对话线程、webhook 推送给我们)抽象的。两种接入形态属于不同的抽象层,11.3/11.9 的所有代码(MessageBus / IM 身份绑定 / CanonicalMessage 归一化)在 widget 场景下都不适用。

为什么现在做:Wave 1 中 11.3/11.9 已交付,只剩 11.2 收尾;开源参考(Intercom、Dify、Salesforce Enhanced Web Chat、Dialogflow Messenger)都用"门户内嵌气泡"做 to-B 默认入口,Hecate 不补这条线,Wave 1 实质上不算交付完成。

## What Changes

- 新增嵌入入口 `/embed/chat?agent=<uuid>` —— 右下角悬浮气泡 + 点开展开窗口,**iframe 模式**(样式天然隔离),浏览器直接调 `/v1/chat/completions`,不引入 webhook 入口,不接 ChannelABC
- 抽出共享 `<ChatSurface>` 组件,被 `(dashboard)/chat/[conversationId]` 和 `/embed/chat` 共同复用,消除逻辑重复;同时抽出 `<ConversationHeader>`(kb / memory / Queued / New Chat)
- 新增气泡外壳组件 `<WidgetBubble>`(右下角 fixed 定位 + 展开/收起动画 + 关闭按钮,关闭后刷新页面重新出现,不做 localStorage 持久化)
- 嵌入页面首次加载自动创建 conversation(`POST /api/conversations`),后续流式调用传 `session_id=conversationId`,与 dashboard chat 行为完全一致
- 鉴权复用员工 JWT(走现有 AuthGuard),JWT 失败按 dashboard 现有逻辑跳 `/login`(iframe 内跳转,本版本接受这个体验)
- 新建 ADR-031 `Web Widget iframe architecture`,记录 widget 不走 ChannelABC 的架构决策,供后续 PR 反驳用

明确**不**包含(对应 P5 deferred 的 11.2 完整版):

- 公开匿名 to-C 场景:WidgetModel、临时 JWT 签发、RS256、Origin 白名单
- `<script src="...">` loader.js 注入模式、CSS Shadow DOM 隔离
- Referer / 父源白名单校验
- i18n / 多语言(本版本仅英文)
- 浮动气泡的"今日不再显示" / localStorage 持久化
- 紧凑的"重新登录"提示(本版本接受 iframe 内跳到 `/login` 的体验)
- 完整版会引入的 WidgetModel 数据表

## Capabilities

### New Capabilities

- `web-widget-access`: Hecate 通过 iframe 嵌入模式暴露 chat 能力,目标场景是 Hecate 自家 portal 和客户内部门户。所有 widget 用户均为已登录 Hecate 员工(复用现有 JWT 鉴权)。浏览器直接调 `/v1/chat/completions`,**不**引入新的 webhook 入口、**不**引入 ChannelABC 集成。

### Modified Capabilities

(本变更不修改任何已存在 capability)

## Impact

**新增代码**:

- `web/src/app/embed/chat/page.tsx`(气泡外壳 + 按需展开的 ChatSurface + AuthGuard 挂载 + `?agent=` 参数读取)
- `web/src/app/embed/chat/embed.module.css`(气泡 / 窗口样式,scope 到本路由)
- `web/src/components/chat/ChatSurface.tsx`(抽出共享组件,接受 `mode` prop)
- `web/src/components/chat/ConversationHeader.tsx`(抽出共享头部)
- `web/src/components/chat/WidgetBubble.tsx`(气泡外壳,展开 / 收起 / 关闭)
- `docs/design/adr/031-web-widget-iframe-architecture.md`(新 ADR)
- `docs/design/adr/INDEX.md`(追加索引)

**修改代码**:

- `web/src/app/(dashboard)/chat/[conversationId]/page.tsx`(改用 ChatSurface + ConversationHeader,验证 dashboard chat 行为完全一致)

**不改**:

- 后端任何文件:`/v1/chat/completions`、`/api/conversations` 已有逻辑足够,本次变更后端零改动
- `src/hecate/channel/adapter.py`(widget 绕过 ChannelABC,ChannelABC 接口本身不动)
- 数据模型(无 Alembic 迁移)

**新增测试**:

- `web/src/__tests__/embed-page.test.tsx`(路由 smoke:加载、气泡渲染、首次 conversation 创建、未指定 agent 提示态、关闭按钮)
- `web/src/__tests__/chat-surface.test.tsx`(组件级:消息渲染、SSE chunk 追加、New Chat 按钮)

**对外 API 变化**: 无(复用现有端点)。

**依赖变化**: 无。

**数据库变化**: 无。
