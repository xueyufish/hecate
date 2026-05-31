## 1. 后端用户系统

- [x] 1.1 创建 `models/user.py` — UserModel (id, email, hashed_password, created_at, updated_at) + Pydantic schemas (RegisterSchema, LoginSchema, UserReadSchema)
- [x] 1.2 创建 Alembic 迁移 — users 表 + unique index on email
- [x] 1.3 创建 `services/auth/password.py` — bcrypt 哈希和验证工具函数
- [x] 1.4 创建 `services/auth/token.py` — JWT access_token (30min) + refresh_token (7d) 生成和验证，使用 python-jose
- [x] 1.5 创建 `services/auth/service.py` — AuthService (register, login, refresh_token, get_current_user)
- [x] 1.6 创建 `api/auth.py` — 4 个端点: POST /api/auth/register, POST /api/auth/login, POST /api/auth/refresh, GET /api/auth/me
- [x] 1.7 修改 `core/deps.py` — 认证中间件支持 JWT Bearer + API Key 双模式（先尝试 JWT，失败再尝试 API Key）
- [x] 1.8 编写用户系统测试 — 注册/登录/刷新/重复邮箱/错误密码/双认证模式

## 2. 前端项目初始化

- [x] 2.1 在 `web/` 目录初始化 Next.js 14 项目 — App Router + TypeScript + Tailwind CSS
- [x] 2.2 安装并配置 shadcn/ui — 添加 Button, Input, Card, Dialog, Table, DropdownMenu 组件
- [x] 2.3 创建 API Client — `lib/api-client.ts`，封装 fetch + Bearer token 注入 + 401 自动刷新
- [x] 2.4 创建 Auth 状态管理 — `lib/auth.tsx`，token 存储 (localStorage) + useAuth hook + ProtectedRoute 组件
- [x] 2.5 创建应用布局 — `app/layout.tsx` (根布局) + `components/sidebar.tsx` (侧边栏导航) + `components/auth-guard.tsx` (认证守卫)

## 3. 登录/注册页面

- [x] 3.1 创建 `app/login/page.tsx` — 登录表单 (email + password) + 调用 login API + 存储 token + 跳转首页
- [x] 3.2 创建 `app/register/page.tsx` — 注册表单 (email + password + confirm) + 调用 register API + 跳转登录页
- [x] 3.3 添加表单验证 — email 格式校验、密码最少 8 位、确认密码一致

## 4. Agent 管理页面

- [x] 4.1 创建 `app/agents/page.tsx` — Agent 列表页，显示名称/模型/创建时间，空状态引导创建
- [x] 4.2 创建 `app/agents/new/page.tsx` — 创建 Agent 表单：名称、描述、模型选择（从 /v1/models 获取）、系统 Prompt
- [x] 4.3 创建 `app/agents/[id]/page.tsx` — Agent 详情/配置页：显示当前配置 + 工具绑定（toggle 列表）+ 知识库绑定（toggle 列表）
- [x] 4.4 创建 `app/agents/[id]/chat/page.tsx` — 跳转到对话页面的入口按钮

## 5. 对话页面

- [x] 5.1 创建 `app/chat/[conversationId]/page.tsx` — 对话主界面：消息列表 + 输入框 + 发送按钮
- [x] 5.2 实现 SSE 流式渲染 — 调用 /v1/chat/completions (stream: true)，逐 token 显示 Agent 回复
- [x] 5.3 实现工具调用展示 — 解析 tool_calls 消息，折叠展示工具名/参数/结果
- [x] 5.4 实现对话历史加载 — 从 /api/conversations/{id} 加载历史消息并渲染
- [x] 5.5 实现新建对话 — "New Chat" 按钮创建新 conversation 并跳转

## 6. 知识库管理页面

- [x] 6.1 创建 `app/knowledge/page.tsx` — 知识库列表页，显示名称/文档数/创建时间
- [x] 6.2 创建 `app/knowledge/new/page.tsx` — 创建知识库表单：名称、描述
- [x] 6.3 创建 `app/knowledge/[id]/page.tsx` — 知识库详情页：文档列表 + 上传按钮 + 解析状态 (pending/parsing/completed/failed) + 错误信息

## 7. 集成与部署

- [x] 7.1 配置 Vite proxy — 开发环境前端 (port 3000) 代理到后端 (port 8000)
- [x] 7.2 更新 `docker/docker-compose.yml` — 添加前端服务容器
- [x] 7.3 端到端冒烟测试 — 注册 → 登录 → 创建 Agent → 对话 → 上传知识库 → 绑定知识库 → 再次对话
