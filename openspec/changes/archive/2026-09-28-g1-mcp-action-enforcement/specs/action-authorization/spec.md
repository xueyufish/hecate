# Spec Delta: action-authorization

## Purpose

跨入口的变更类动作授权契约:本 change 覆盖的 REST Agent 写操作和 MCP 变更工具必须得到一致的角色判定,执行上下文(身份、租户、资源根)由服务端从已验证凭据派生,共享种子资源对非系统身份只读。本能力是 step7 完整 Action 应用服务的授权基础,不表示未覆盖入口已统一。

## ADDED Requirements

### Requirement: Consistent role enforcement across entry points
对本 change 覆盖的变更类动作(REST Agent create/update/delete/import、Agent 技能关联/晋升/移除，以及 MCP agent、knowledge、tool 变更工具),系统 SHALL 要求调用者持有显式 workspace 的 editor 及以上角色。viewer 角色或无 workspace 成员资格的调用者 SHALL 在对应入口被拒绝,且拒绝发生 SHALL 先于任何数据库、向量存储或其他持久化副作用。对同一 Agent 变更,REST 与 MCP 的授权结果 SHALL 一致。其他入口尚未覆盖,不得据此推断已满足此要求。

#### Scenario: Viewer write rejected identically on REST and MCP
- **WHEN** a viewer-role identity attempts the same agent mutation once via the REST API and once via the MCP tool
- **THEN** both entry points reject the call with an authorization error and no state change occurs on either path

#### Scenario: Editor write allowed on both entry points
- **WHEN** an editor-role identity performs the same agent mutation via the REST API and via the MCP tool
- **THEN** both entry points perform the mutation scoped to the caller's workspace

### Requirement: Server-derived execution context for tool actions
工具执行入口 SHALL 从服务端已验证的身份上下文派生租户与文件资源根,SHALL NOT 信任调用者提供的工作区根、路径前缀或身份声明。授权检查 SHALL 与最终执行绑定到同一个唯一解析的工具记录/定义;歧义、来源不一致或授权元数据缺失 SHALL 在副作用前拒绝。文件类工具的副作用 SHALL 限定在调用者 workspace 对应的资源根内;解析后指向根外的路径(包括符号链接) SHALL 拒绝,跨 workspace 的文件读取与写入 SHALL 被隔离。

#### Scenario: Caller-supplied context cannot widen access
- **WHEN** a caller includes a workspace identifier or absolute path belonging to another workspace in a tool execution request
- **THEN** the server ignores the caller-supplied values and the execution stays within the server-derived workspace scope

### Requirement: Shared seed resources are read-only for tenants
被明确标记为共享只读的 zero workspace 种子资源 SHALL 按资源类型对 workspace 成员可读;不得推断所有 zero workspace 资源均可通过列表或 ID 查询访问。非系统身份的写操作 SHALL NOT 落入 zero workspace——写操作的 workspace 归属 SHALL 来自调用者的显式 workspace 成员资格,缺失时 SHALL 拒绝而不是回退到共享空间。system scope 写入必须显式指定目标 workspace。

#### Scenario: Write without explicit workspace does not pollute shared catalog
- **WHEN** an authenticated identity without a workspace membership attempts a mutating operation whose owner would default to the zero workspace
- **THEN** the operation is rejected with an explicit error and the shared zero-workspace catalog is unchanged

#### Scenario: Shared read-only visibility is resource-specific
- **WHEN** a workspace member lists or reads a zero-workspace resource
- **THEN** only resource types explicitly designated as shared read-only are visible, and mutation remains denied

### Requirement: Unauthorizable actions fail closed
在缺少可验证授权所需信息的入口(如无审批绑定路径、无服务端资源根、工具解析歧义或缺少执行主体上下文),系统 SHALL 对无法安全授权的动作显式拒绝,SHALL NOT 以静默执行或占位成功代替。`approval_required=true`、风险级别为 HIGH 或风险元数据未知的动作 SHALL 在副作用前拒绝。被拒绝的动作 SHALL 产生可观察的错误结果。

#### Scenario: Tool lacking authorization prerequisites rejected
- **WHEN** an entry point is asked to execute an action whose approval binding or resource scope cannot be established server-side
- **THEN** the entry point returns an explicit rejection before any side effect, instead of executing the action unprotected
