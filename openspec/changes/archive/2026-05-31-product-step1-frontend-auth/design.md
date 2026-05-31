## Context

Hecate 后端已完成 P1-P4（FastAPI + Pregel 引擎 + RAG + MCP + 安全），有完整的 REST API，但：
- 没有前端界面——用户无法通过浏览器使用系统
- 没有用户认证——只有 API Key 机制，没有注册/登录
- 当前状态是"强大的 Python 库"，不是"可部署的产品"

需要建立最小可用闭环：注册 → 登录 → 创建 Agent（选模型/配工具/挂知识库）→ 对话测试。

## Goals / Non-Goals

**Goals:**
1. 用户注册/登录 + JWT 认证（access + refresh token）
2. Next.js 前端项目初始化 + 基础布局 + 中文界面
3. Agent 管理界面（列表/创建/配置模型和工具/绑定知识库）
4. 对话界面（SSE 流式输出 + 工具调用过程展示）
5. 知识库管理界面（列表/创建/上传文档/查看解析状态）

**Non-Goals:**
1. 不做多租户/RBAC——单用户阶段
2. 不做工作流画布——第二步
3. 不做多 Agent 编排界面——第二步
4. 不做多渠道接入（飞书/企微）——后续
5. 不做 SSO/LDAP——企业阶段
6. 不做移动端适配——先保证桌面端

## Decisions

### D1: Next.js 14 App Router + shadcn/ui

**选择**：Next.js App Router（非 Pages Router）+ shadcn/ui + Tailwind CSS

**理由**：
- App Router 是 Next.js 未来方向，支持 Server Components
- shadcn/ui 可定制、不锁入、社区活跃（相比 Ant Design 更现代）
- Tailwind CSS 开发效率高，和 shadcn/ui 天然搭配

**替代方案**：Ant Design（企业级但定制难）、Vue3 + Element Plus（团队不熟 Vue）

### D2: JWT 双 Token（access + refresh）

**选择**：access_token（30min）+ refresh_token（7d），使用 python-jose

**理由**：
- 无状态认证，适合 API 服务
- refresh_token 延长会话，减少登录频率
- 向后兼容 API Key——中间件同时支持两种认证

**替代方案**：Session-based（需要服务端状态，不适合 API）、OAuth2（过于复杂）

### D3: 前端放在 web/ 目录（Monorepo）

**选择**：前后端同仓库，`web/` 目录存放 Next.js 项目

**理由**：
- 统一版本管理，一次 commit 包含前后端改动
- 共享 OpenSpec 变更文档
- CI/CD 统一

### D4: 前端直接调用后端 API（不通过 BFF）

**选择**：前端 Next.js API Client 直接调用 Hecate FastAPI 后端

**理由**：
- 减少 BFF 层的复杂度
- 后端已有 CORS 配置
- 开发阶段用 Vite proxy 转发，部署时反向代理

### D5: 密码安全

**选择**：bcrypt 哈希，不存储明文密码

**理由**：
- bcrypt 是业界标准，自带 salt
- passlib 或 bcrypt 库直接可用

## Risks / Trade-offs

| 风险 | 缓解 |
|------|------|
| SSE 流式输出跨域问题 | 开发时 Vite proxy，部署时 Nginx 反向代理 |
| JWT 无状态导致无法主动踢人 | 第一步可接受，后续引入黑名单 |
| shadcn/ui 组件不够用 | 可用 Radix UI 原语扩展 |
| 前端首次搭建耗时 | 聚焦核心页面，跳过花哨动画 |
