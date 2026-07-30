## ADDED Requirements — 新增需求

### Requirement: Next.js project initialization — 需求：Next.js 项目初始化
系统应在 `web/` 目录下拥有 Next.js 14 项目，配置 App Router、shadcn/ui 和 Tailwind CSS。

#### Scenario: Project structure — 场景：项目结构
- **WHEN** 开发者打开 `web/` 目录
- **THEN** 它包含一个有效的 Next.js 项目，包含 app/ router、components/ 目录和已安装的 shadcn/ui

### Requirement: Application layout — 需求：应用布局
系统应提供响应式桌面布局，包含侧边栏导航和主内容区。

#### Scenario: Authenticated layout — 场景：已认证布局
- **WHEN** 用户已登录
- **THEN** 侧边栏显示导航链接：Agents、Knowledge Bases，主区域显示当前页面

#### Scenario: Unauthenticated redirect — 场景：未认证重定向
- **WHEN** 用户未登录且访问受保护页面
- **THEN** 系统重定向到登录页面

### Requirement: API client — 需求：API 客户端
系统应提供类型化的 API 客户端，处理认证、错误响应和 SSE 流式传输。

#### Scenario: Authenticated requests — 场景：已认证请求
- **WHEN** API 客户端发起请求
- **THEN** 它将当前的 access_token 作为 Bearer 头附加

#### Scenario: Token auto-refresh — 场景：Token 自动刷新
- **WHEN** API 请求返回 401，表示 access_token 过期
- **THEN** 客户端自动刷新 Token 并重试请求

### Requirement: Auth state management — 需求：Auth 状态管理
系统应使用安全存储跨页面刷新持久化认证状态。

#### Scenario: Login persistence — 场景：登录持久化
- **WHEN** 用户登录后刷新页面
- **THEN** 用户保持登录状态（Token 从存储中恢复）

#### Scenario: Token expiry — 场景：Token 过期
- **WHEN** access 和 refresh Token 都过期
- **THEN** 系统重定向到登录页面
