## 1. 数据模型与迁移

- [x] 1.1 创建 `src/hecate/models/organization.py`，包含 OrganizationModel（id, name, slug, owner_id FK, settings JSON）、OrganizationCreateSchema、OrganizationUpdateSchema、OrganizationReadSchema
- [x] 1.2 创建 `src/hecate/models/workspace.py`，包含 WorkspaceModel（id, org_id FK, name, slug, settings JSON）、WorkspaceCreateSchema、WorkspaceUpdateSchema、WorkspaceReadSchema
- [x] 1.3 创建 `src/hecate/models/workspace_member.py`，包含 WorkspaceMemberModel（id, user_id FK, workspace_id FK, role 枚举）、成员 CRUD schema 以及 WorkspaceRole 枚举（admin/editor/viewer）
- [x] 1.4 创建 `src/hecate/models/api_key.py`，包含 ApiKeyModel（id, name, key_hash, key_prefix, scope 枚举, org_id 可空 FK, workspace_id 可空 FK, created_by FK, last_used_at, expires_at, is_active）、ApiKeyScope 枚举（system/workspace）、密钥 CRUD schema
- [x] 1.5 更新 `src/hecate/models/user.py`：为 UserModel 添加可选的 `sso_id` 字段（可空 String），为 UserReadSchema 添加 `sso_id`
- [x] 1.6 在 `src/hecate/models/__init__.py` 中注册新模型，使 conftest 的 `Base.metadata.create_all()` 能识别
- [x] 1.7 为 7 个现有模型（AgentModel、WorkflowModel、SkillModel、ToolModel、KnowledgeBaseModel、PromptModel、MemoryBlockModel、MemoryModel、KnowledgeMemoryModel）的 workspace_id 添加指向 WorkspaceModel.id 的外键约束
- [x] 1.8 创建 Alembic 迁移：引导默认组织（零 UUID）、默认工作区（零 UUID）、为现有模型添加 workspace_id FK 约束

## 2. 认证上下文与依赖注入

- [x] 2.1 创建 `src/hecate/core/auth_context.py`，包含 AuthContext 数据类（user_id, org_id, workspace_id, role, auth_method, api_key_scope）和 WorkspaceRole 枚举重导出
- [x] 2.2 创建 `src/hecate/core/deps_workspace.py`，包含 `get_auth_context()` 依赖项，将 JWT 或 API 密钥解析为 AuthContext，替代 verify_api_key 和 get_current_user_id
- [x] 2.3 添加 RBAC 依赖函数：`require_workspace_admin()`、`require_workspace_editor()`、`require_workspace_viewer()`——各自检查 AuthContext.role 是否满足最低要求角色
- [x] 2.4 更新 `src/hecate/services/auth/token.py`：扩展 `create_access_token()` 以接受并编码 org_id、workspace_id、role 声明；更新 `decode_access_token()` 以提取新声明
- [x] 2.5 更新 `src/hecate/services/auth/service.py`：login 方法解析用户的第一个工作区成员资格并将其包含在令牌声明中；在登录响应中添加工作区列表
- [x] 2.6 在 `src/hecate/api/auth.py` 中添加 `/auth/switch-workspace` 端点：验证成员资格，签发包含更新工作区声明的新令牌
- [x] 2.7 更新 `src/hecate/models/user.py` schema：在登录响应中添加工作区列表（TokenResponseSchema 或新的 LoginResponseSchema）

## 3. API 密钥管理

- [x] 3.1 创建 `src/hecate/services/api_key_service.py`，包含 ApiKeyService：create_key（生成 hcat_ 前缀、SHA-256 哈希、存储）、verify_key（哈希查找、更新 last_used_at）、rotate_key、revoke_key、list_keys
- [x] 3.2 创建 `src/hecate/api/management/api_keys.py` 路由，包含端点：POST /api/api-keys、GET /api/api-keys、GET /api/api-keys/{id}、DELETE /api/api-keys/{id}、POST /api/api-keys/{id}/rotate
- [x] 3.3 将 ApiKeyService 集成到 `get_auth_context()` 依赖项中：在环境变量回退之前先进行数据库密钥查找，对环境变量密钥发出弃用警告
- [x] 3.4 更新 `src/hecate/core/config.py`：为 HECATE_API_KEYS 添加弃用警告属性

## 4. 组织与工作区服务

- [x] 4.1 创建 `src/hecate/services/organization_service.py`，包含 OrganizationService：create、get、list（按所有者）、update、soft_delete、transfer_ownership
- [x] 4.2 创建 `src/hecate/services/workspace_service.py`，包含 WorkspaceService：create（自动将创建者添加为 admin）、get、list（按组织 + 成员资格）、update、soft_delete（含资源检查）
- [x] 4.3 创建 `src/hecate/services/workspace_member_service.py`，包含 WorkspaceMemberService：add_member、remove_member、update_role、list_members、check_role
- [x] 4.4 创建 `src/hecate/api/management/orgs.py` 路由，包含端点：POST /api/orgs、GET /api/orgs、GET /api/orgs/{org_id}、PATCH /api/orgs/{org_id}、DELETE /api/orgs/{org_id}、POST /api/orgs/{org_id}/transfer-ownership
- [x] 4.5 创建 `src/hecate/api/management/workspaces.py` 路由，包含端点：POST /api/orgs/{org_id}/workspaces、GET /api/orgs/{org_id}/workspaces、GET /api/orgs/{org_id}/workspaces/{ws_id}、PATCH /api/orgs/{org_id}/workspaces/{ws_id}、DELETE /api/orgs/{org_id}/workspaces/{ws_id}
- [x] 4.6 创建工作区成员端点：POST /api/orgs/{org_id}/workspaces/{ws_id}/members、DELETE /api/orgs/{org_id}/workspaces/{ws_id}/members/{user_id}、PATCH /api/orgs/{org_id}/workspaces/{ws_id}/members/{user_id}、GET /api/orgs/{org_id}/workspaces/{ws_id}/members

## 5. API 端点迁移

- [x] 5.1 更新 `src/hecate/api/management/agents.py`：将 Depends(verify_api_key) 替换为 Depends(get_auth_context)，在所有查询中添加 workspace_id 过滤，在创建时从认证上下文中设置 workspace_id
- [x] 5.2 更新 `src/hecate/api/management/workflows.py`：与 agents 相同模式——认证上下文 + 工作区限定
- [x] 5.3 更新 `src/hecate/api/management/skills.py`：相同模式
- [x] 5.4 更新 `src/hecate/api/management/tools.py`：相同模式
- [x] 5.5 更新 `src/hecate/api/management/knowledge.py`：相同模式
- [x] 5.6 更新 `src/hecate/api/management/prompts.py`：相同模式
- [x] 5.7 更新 `src/hecate/api/management/memory.py`：相同模式 + 认证上下文 workspace_id 替代请求参数
- [x] 5.8 更新 `src/hecate/api/v1/chat.py`：从认证上下文注入 workspace_id 到会话服务调用
- [x] 5.9 在 `src/hecate/main.py` 中注册新路由：orgs、workspaces、api_keys

## 6. 服务层更新

- [x] 6.1 更新所有接受 workspace_id 参数的 service 方法，使其从认证上下文而非请求体获取：AgentService、WorkflowService、SkillService、ToolService、KnowledgeService、PromptService、MemoryService
- [x] 6.2 更新 session-memory 集成：确保 ConversationService 将认证上下文的 workspace_id 传递给 WorkingMemoryService、UserMemoryService、KnowledgeMemoryService 调用
- [x] 6.3 更新所有资源服务中的 WorkspaceService 查询，按认证的 workspace_id 过滤

## 7. 路由注册与弃用

- [x] 7.1 保留旧的 `verify_api_key` 和 `get_current_user_id` 作为 `get_auth_context()` 的薄封装，在过渡期间保持向后兼容
- [x] 7.2 在 verify_api_key 中为环境变量 API 密钥路径添加弃用日志记录

## 8. 测试

- [x] 8.1 创建 `tests/test_models/test_organization.py`：测试 OrganizationModel 创建、slug 自动生成、唯一 slug 约束、软删除
- [x] 8.2 创建 `tests/test_models/test_workspace.py`：测试 WorkspaceModel 创建、组织 FK、组织内 slug 唯一性、带资源检查的软删除
- [x] 8.3 创建 `tests/test_models/test_workspace_member.py`：测试成员 CRUD、唯一 user-workspace 约束、角色枚举验证、最后一个管理员保护
- [x] 8.4 创建 `tests/test_models/test_api_key.py`：测试密钥生成格式（hcat_ 前缀）、哈希存储、前缀提取、作用域验证、过期检查
- [x] 8.5 创建 `tests/test_api/test_org_api.py`：测试 CRUD 端点、所有权转移、权限强制（非所有者不能更新）
- [x] 8.6 创建 `tests/test_api/test_workspace_api.py`：测试 CRUD 端点、成员管理、工作区限定的资源列表
- [x] 8.7 创建 `tests/test_api/test_rbac_api.py`：测试基于角色的访问——viewer 拒绝创建、editor 拒绝成员管理、admin 完全访问
- [x] 8.8 创建 `tests/test_api/test_api_key_api.py`：测试密钥创建、轮换、撤销、验证、作用域强制
- [x] 8.9 创建 `tests/test_api/test_auth_workspace.py`：测试增强的 JWT 声明、切换工作区端点、登录工作区列表、带工作区上下文的刷新
- [x] 8.10 更新 `tests/test_api/test_auth.py`：使现有测试适应新的 JWT 声明结构（登录返回工作区列表，令牌包含 org/ws 声明）
- [x] 8.11 更新 `tests/conftest.py`：为 get_auth_context 添加 dependency_overrides，创建用于 org、workspace、member、api_key 的辅助 fixture

## 9. 验证

- [x] 9.1 运行 `ruff check src/hecate/ tests/` —— 零错误
- [x] 9.2 运行 `ruff format --check src/ tests/` —— 零错误
- [x] 9.3 运行 `mypy src/` —— 零错误
- [x] 9.4 运行 `python -m pytest tests/ -q` —— 所有测试通过
