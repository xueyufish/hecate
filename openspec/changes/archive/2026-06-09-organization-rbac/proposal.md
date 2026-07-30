## Why — 原因

Hecate 目前以单工作区模式运行——所有资源共享一个零 UUID 的工作区，API 密钥是无作用域的环境变量，没有组织或角色模型。这阻碍了企业级采用：客户需要多租户隔离、工作区级访问控制和可审计的 API 密钥管理。当前的认证系统（仅包含 `sub` 声明的 JWT + 全局 API 密钥）无法表达"此用户可以编辑器身份访问工作区 X"或"此 API 密钥仅限工作区 Y"。

本次变更合并了功能 10.1（组织管理）和 10.2（RBAC），因为组织结构和访问控制紧密耦合——没有先建立组织→工作区层级结构，就无法实施工作区级角色。

## What Changes — 变更内容

- **Organization 模型**：新增 `OrganizationModel`（id, name, slug, settings），代表企业客户。扁平结构——无嵌套部门。部门层级由外部 OA/IAM 系统管理，同步到 Hecate 作为工作区级映射。
- **Workspace 模型**：新增 `WorkspaceModel`（id, org_id FK, name, slug, settings），作为资源隔离边界。每个工作区属于且仅属于一个组织。
- **Workspace 成员资格**：新增 `WorkspaceMemberModel`（user_id, workspace_id, role），包含三个角色：`admin`、`editor`、`viewer`。替代了隐式的"每个人都能访问所有资源"模型。
- **API 密钥管理**：新增 `ApiKeyModel`（key_hash, key_prefix, scope, org_id?, workspace_id?），存储于数据库中。包含两个作用域：`system`（跨组织平台管理员）和 `workspace`（单工作区操作）。**破坏性变更**：替代当前的 `HECATE_API_KEYS` 环境变量方式。
- **JWT 声明扩展**：访问令牌新增 `org_id`、`workspace_id` 和 `role` 声明。当前的仅含 `sub` 的 JWT 不足以支持工作区级授权。
- **依赖注入增强**：`verify_api_key()` 和 `get_current_user_id()` 新增工作区解析——下游代码接收经过认证的上下文（org, workspace, role），而非裸用户 ID 或令牌字符串。
- **工作区强制实施**：所有 7 个现有包含 `workspace_id` 的模型（Agent、Workflow、Skill、Tool、KnowledgeBase、Prompt、Memory*）新增外键约束，并通过认证的工作区上下文进行过滤。

## Capabilities — 能力

### 新增能力

- `org-workspace`：组织和工作区的 CRUD、生命周期管理（创建、读取、更新、软删除）、基于 slug 的寻址和工作区设置。涵盖 OrganizationModel、WorkspaceModel 及其 API 端点和服务层。
- `rbac`：工作区级基于角色的访问控制，包含三个角色（admin、editor、viewer）。涵盖 WorkspaceMemberModel、角色分配/撤销、通过 FastAPI 依赖项进行的权限检查，以及基于角色的查询过滤。
- `api-key-management`：基于数据库的 API 密钥生命周期——创建、轮换、撤销、作用域限定（system/workspace）、上次使用跟踪和可选过期。替代环境变量 `HECATE_API_KEYS` 方式。
- `auth-enhancement`：JWT 声明扩展（org_id、workspace_id、role）、携带工作区上下文的令牌刷新、返回可访问工作区的登录流程，以及为未来 OIDC/SAML 集成提供的 SSO 扩展点。

### 修改的能力

- `memory-api`：必须将 JWT/工作区认证上下文传递到内存端点，使内存块限定于经过认证的工作区，而非接受请求体中的任意 workspace_id。
- `session-memory`：会话内存工具必须从认证上下文自动注入 workspace_id，而非从请求参数中获取。

## Impact — 影响

**模型（新增）**：OrganizationModel、WorkspaceModel、WorkspaceMemberModel、ApiKeyModel——4 个新 ORM 模型，4 个新 Alembic 迁移。

**模型（修改）**：7 个现有模型（Agent、Workflow、Skill、Tool、KnowledgeBase、Prompt、Memory*）在 workspace_id 上新增指向 WorkspaceModel 的外键约束。需要对现有行进行数据迁移（当前均为零 UUID——必须分配到一个默认工作区）。

**核心层**：`deps.py`（重写 verify_api_key、get_current_user_id），`config.py`（弃用 HECATE_API_KEYS 环境变量，添加默认工作区/组织引导）。

**服务层**：`auth/service.py` 和 `auth/token.py` 新增工作区感知的登录和令牌创建。新增 `OrganizationService`、`WorkspaceService`、`ApiKeyService`。

**API 层**：新增路由 —— `/api/orgs`、`/api/workspaces`、`/api/api-keys`。修改的路由 —— `/api/agents`、`/api/workflows`、`/api/skills`、`/api/tools`、`/api/knowledge-bases`、`/api/prompts`、`/api/memory` 新增工作区级过滤。

**测试**：为组织、工作区、RBAC、API 密钥流程新增测试文件。更新现有认证测试以适配新的 JWT 声明。

**向后兼容**：引导迁移创建默认组织 + 默认工作区（使用零 UUID ID），确保现有单租户部署无需配置更改即可继续运行。
