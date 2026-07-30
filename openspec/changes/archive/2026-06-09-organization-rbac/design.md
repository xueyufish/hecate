## Context — 背景

Hecate 目前以单租户模式运行：所有资源共享零 UUID 工作区，API 密钥是逗号分隔的环境变量，JWT 令牌仅携带 `sub`（user_id），没有组织或角色概念。七个模型已经包含 `workspace_id` 列（普通 UUID，无外键，默认零 UUID），但从未被认证层强制实施。

认证栈结构如下：
- **JWT**：HS256，30 分钟访问 / 7 天刷新，声明 = `{sub, type, exp, iat}`。密钥由第一个 API 密钥派生。
- **API 密钥**：`HECATE_API_KEYS` 环境变量 → `settings.api_keys_list`。在 `verify_api_key()` 中以纯文本字符串匹配方式进行校验。API 密钥认证为 user_id 返回占位符 UUID `00000000-...`。
- **依赖注入**：`verify_api_key()`（双 JWT+密钥），`get_current_user_id()`（提取 sub 或返回占位符），`get_current_agent()`（404 查找）。
- **无 RBAC**：每个经过认证的请求对所有资源拥有完整访问权限。

外部参考资料：
- **Dify**：`ApiToken` 包含 `tenant_id`（必填）+ 可选的 `app_id` 用于更精细的作用域。简单的两级密钥模型。
- **AgentScope**：通过 `user_id` 路由实现多租户，`LocalWorkspaceManager` 实现按代理的目录隔离。无组织层级。
- **Salesforce Agentforce**：继承平台 IAM（组织 → 用户 → 权限集）。对于自托管方案过于沉重。
- **openJiuwen**：在网关层使用 AK/SK + 通道令牌，会话亲和调度实现多租户隔离。组织层级由外部管理。

设计原则：Hecate 不管理组织层级结构（部门、团队）。组织结构由外部 OA/IAM 系统管理并同步到 Hecate。Hecate 提供隔离边界（工作区）和访问控制（角色），而非组织架构图。

## Goals / Non-Goals — 目标 / 非目标

**目标：**

- 建立扁平组织→工作区的层级结构作为多租户隔离模型。
- 实现工作区级 RBAC，包含 3 种角色（admin、editor、viewer）。
- 用基于数据库的、有作用域的 API 密钥（system + workspace）替换环境变量 API 密钥。
- 在 JWT 令牌中增加 org/workspace/role 声明，用于下游授权。
- 为现有模型添加工作区外键约束，并强制实施工作区级查询。
- 提供向后兼容的引导（使用零 UUID ID 的默认组织 + 默认工作区）。
- 为未来 SSO（OIDC/SAML）和团队/组层的扩展点预留空间。

**非目标：**

- 嵌套组织层级（部门、子部门）。非 Hecate 职责范围。
- 组织和工作区之间的团队/组模型。可后续作为纯增量添加。
- SSO 集成（OIDC/SAML）。推迟到未来的变更；当前设计仅添加 `sso_id` 字段作为扩展点。
- 细粒度的资源级权限（例如"可以编辑代理但不能编辑工作流"）。工作区级 3 角色模型目前足够。
- 计算/网络层面的租户隔离（功能 10.5）。本次变更仅涵盖数据隔离。
- 按工作区的速率限制。当前的按令牌速率限制保持不变。

## Decisions — 决策

### D1：扁平组织→工作区层级结构

**决策**：两级模型：Organization（企业客户）→ Workspace（资源隔离单元）。

**考虑的替代方案**：
- **嵌套组织（组织→子组织→工作区）**：需要递归查询，权限继承复杂。组织层级不是 Hecate 的职责——由外部 OA/IAM 管理。
- **组织 + 团队（组织→团队→工作区）**：额外的部门分组层。YAGNI——当前无用例需要。后续添加团队层是纯粹的增量（无需更改现有表结构）。

**理由**：扁平模型符合行业模式（Dify 的 tenant → app，Coze 的 space → project）。来自 OA/IAM 的部门层级在同步时被扁平化为工作区映射。

### D2：工作区作为隔离边界

**决策**：所有租户范围的资源属于且仅属于一个工作区。现有模型上的 workspace_id 获取指向 WorkspaceModel 的外键。

**理由**：7 个模型已包含 workspace_id 列。零 UUID 默认值是 P1 的占位符。将其升级为带强制约束的真实外键是实现多租户的最小变更。

**对现有模型的影响**：AgentModel、WorkflowModel、SkillModel、ToolModel、KnowledgeBaseModel、PromptModel、MemoryBlockModel、MemoryModel、KnowledgeMemoryModel——全部新增外键约束。workspace_id 为零 UUID 的现有行通过引导迁移分配给默认工作区。

### D3：工作区级别的 3 角色 RBAC

**决策**：WorkspaceMemberModel 使用枚举角色：`admin`（管理工作区 + 成员 + 所有资源）、`editor`（创建/编辑/删除资源）、`viewer`（只读访问）。

**考虑的替代方案**：
- **2 角色（owner/member）**：对企业来说过于粗粒度。Viewer 角色对审计/合规至关重要。
- **自定义角色构建器**：对当前需求来说过度设计。3 个固定角色覆盖 90% 的用例。

**权限矩阵**：

| 操作 | admin | editor | viewer |
|--------|-------|--------|--------|
| 管理工作区设置 | ✅ | ❌ | ❌ |
| 管理成员（邀请/移除） | ✅ | ❌ | ❌ |
| 创建资源 | ✅ | ✅ | ❌ |
| 编辑/删除资源 | ✅ | ✅ | ❌ |
| 读取资源 | ✅ | ✅ | ✅ |
| 管理 API 密钥 | ✅ | ❌ | ❌ |
| 删除工作区 | ✅ | ❌ | ❌ |

### D4：2 级 API 密钥作用域

**决策**：API 密钥具有 `scope` 枚举：`system`（跨组织平台管理员）或 `workspace`（单个工作区）。无 `org` 作用域。

**考虑的替代方案**：
- **3 级（system/org/workspace）**：组织级密钥增加复杂性，收益甚微。组织管理员可在浏览器中使用 JWT 认证。
- **单级（所有密钥同等）**：不足以实现多租户隔离。

**密钥存储**：存储 SHA-256 哈希 + 8 字符前缀。原始密钥仅在创建时显示一次。符合行业最佳实践（Dify、GitHub 令牌）。

**密钥格式**：`hcat_<base62_random_32chars>`——前缀用于识别，长度足以保证熵值。

### D5：JWT 声明增强

**决策**：访问令牌新增 `org_id`、`workspace_id`、`role` 声明，与现有的 `sub`、`type`、`exp`、`iat` 一起。

**新的 JWT 结构**：
```json
{
  "sub": "user-uuid",
  "type": "access",
  "org_id": "org-uuid",
  "workspace_id": "workspace-uuid",
  "role": "editor",
  "exp": "...",
  "iat": "..."
}
```

**理由**：下游代码需要工作区上下文，而无须每次请求都查询数据库。JWT 声明提供 O(1) 的认证上下文访问。

**登录流程变更**：`/auth/login` 返回限定于用户"当前"工作区的令牌。新增 `/auth/switch-workspace` 端点用于更改活动工作区（签发新令牌）。

### D6：依赖注入重构

**决策**：用统一的 `get_auth_context()` 依赖项替换 `verify_api_key()` 和 `get_current_user_id()`，返回类型化的 `AuthContext` 数据类。

```python
@dataclass
class AuthContext:
    user_id: uuid.UUID
    org_id: uuid.UUID
    workspace_id: uuid.UUID
    role: WorkspaceRole | None  # 系统级 API 密钥为 None
    auth_method: Literal["jwt", "api_key"]
    api_key_scope: ApiKeyScope | None  # JWT 认证为 None
```

**迁移策略**：现有的 `Depends(verify_api_key)` 和 `Depends(get_current_user_id)` 被替换为 `Depends(get_auth_context)`。过渡期间保持向后兼容——旧的依赖项在迁移期内保留为薄封装。

### D7：引导迁移

**决策**：迁移创建默认组织（`slug: "default"`, id: 零 UUID）和默认工作区（`slug: "default"`, id: 零 UUID）。所有含零 UUID workspace_id 的现有行保持有效。

**理由**：单租户部署的零停机迁移。现有数据自动属于默认工作区。workspace_id 列无需数据回填。

### D8：组织所有权

**决策**：每个组织至少有一个 `owner`——存储为 `OrganizationModel.owner_id`（指向 UserModel 的外键）。所有者是该组织下所有工作区的初始管理员。

**理由**：组织需要有一个负责生命周期管理（计费、删除）的责任人。所有者在组织创建时设置，且可转让。

## Risks / Trade-offs — 风险 / 权衡

**[R1] API 密钥的破坏性变更** → `HECATE_API_KEYS` 环境变量被弃用。现有部署必须通过新的管理端点创建 API 密钥。**缓解措施**：在弃用窗口期（v0.x）同时支持环境变量和数据库密钥。使用环境变量密钥时记录警告。

**[R2] workspace_id 的外键约束** → 使用零 UUID 的现有行必须引用有效的工作区。**缓解措施**：引导迁移在添加外键约束之前创建默认工作区。

**[R3] JWT 令牌大小增加** → 新增 3 个声明（org_id、workspace_id、role）增加约 120 字节。对于 HTTP 头部来说可以忽略。

**[R4] 工作区成员表成为瓶颈** → 每个经过认证的请求都需要成员查询。**缓解措施**：JWT 声明嵌入角色，避免每次请求都查询数据库。仅在令牌签发（登录、切换工作区）时检查成员资格。

**[R5] 向后兼容的单一默认工作区** → 如果多个用户共享一个实例，他们都落入同一个默认工作区。**缓解措施**：这是当前行为（零 UUID 工作区）。管理员可在升级后创建组织/工作区并迁移资源。

## Migration Plan — 迁移计划

1. **Phase 1 — 模式**（本次变更）：创建新模型（Organization、Workspace、WorkspaceMember、ApiKey）。为现有模型添加外键约束。引导默认组织/工作区。
2. **Phase 2 — 认证增强**（本次变更）：扩展 JWT 声明。实现 `get_auth_context()`。更新所有 API 端点以使用工作区级查询。
3. **Phase 3 — 弃用**（下一版本）：移除 `HECATE_API_KEYS` 环境变量支持。移除旧的 `verify_api_key()` / `get_current_user_id()`。

**回滚**：迁移是可逆的——降级将删除新表和 FK 约束，恢复零 UUID 默认值。

## Open Questions — 开放问题

- **工作区切换用户体验**：`/auth/switch-workspace` 应该直接接受工作区 UUID，还是支持"切换到组织 X"（列出可用工作区）？倾向于明确的工作区 UUID 以保持简单。
- **API 密钥轮换**：密钥轮换应该立即创建新密钥（使旧密钥失效）还是允许两个活跃密钥的宽限期？倾向于立即轮换以确保安全。
- **系统 API 密钥所有权**：系统级密钥绕过工作区成员检查。它们应该局限于特殊的"系统"组织还是单独管理？倾向于系统密钥不与组织关联。
