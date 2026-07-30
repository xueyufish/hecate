## Why — 动机

Hecate 后端引擎已完成（P1-P4），但系统没有前端界面和用户认证——用户无法通过浏览器创建 Agent、配置工具/知识库、进行对话测试。当前状态是"一个强大的 Python 库"，不是"一个可部署的产品"。第一步需要建立最小可用闭环：注册 → 登录 → 创建 Agent → 对话。

## What Changes — 变更内容

- 新增 Next.js 14 前端项目（`web/` 目录），使用 shadcn/ui + Tailwind CSS，中文界面
- 新增后端 User 模型（`users` 表）+ JWT 认证体系（注册/登录/刷新 Token）
- 新增 4 个认证 API：`POST /api/auth/register`、`POST /api/auth/login`、`POST /api/auth/refresh`、`GET /api/auth/me`
- 修改现有 API 认证中间件：支持 JWT Bearer Token（同时保留 API Key 向后兼容）
- 新增前端页面：登录/注册、Agent 管理（列表/创建/配置）、对话（SSE 流式）、知识库管理（列表/创建/上传文档）

## Capabilities — 能力

### New Capabilities — 新增能力
- `user-auth`：用户注册、登录、JWT Token 管理（access + refresh）、当前用户信息查询
- `frontend-foundation`：Next.js 项目初始化、布局组件、路由结构、API Client 封装、Auth 状态管理
- `agent-management-ui`：Agent 列表、创建、配置（模型选择/系统 Prompt/工具绑定/知识库绑定）的前端界面
- `chat-ui`：与 Agent 实时对话界面，SSE 流式输出，工具调用过程展示，对话历史
- `knowledge-base-ui`：知识库列表、创建、文档上传、解析状态展示

### Modified Capabilities — 修改的能力

（无 — 现有 API 行为不变，只是认证方式扩展）

## Impact — 影响范围

- **Backend**：新增 `models/user.py`、`services/auth/`、`api/auth.py`；修改 `core/deps.py` 认证中间件支持 JWT + API Key 双模式
- **Frontend**：新增 `web/` 目录（Next.js 项目），约 10-15 个页面/组件
- **Dependencies**：后端新增 `bcrypt`、`PyJWT`（或 `python-jose`）；前端 Next.js 生态
- **Database**：新增 `users` 表 + Alembic 迁移
- **Deployment**：前端需要独立构建和部署（或反向代理到同一域名）
