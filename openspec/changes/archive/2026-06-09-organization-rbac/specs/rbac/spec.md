## ADDED Requirements — 新增需求

### 需求：工作区成员模型
系统应维护一个 `WorkspaceMemberModel`，将用户链接到工作区并赋予角色。每个用户-工作区对应是唯一的。角色应为以下之一：`admin`、`editor`、`viewer`。

#### 场景：用户被添加到工作区
- **当** 工作区管理员发送 POST `/api/orgs/{org_id}/workspaces/{ws_id}/members`，包含 `{user_id: "...", role: "editor"}`
- **则** 系统创建 WorkspaceMemberModel 条目并返回 `201`

#### 场景：重复成员被拒绝
- **当** 工作区管理员尝试添加已是工作区成员的用户
- **则** 系统返回 `409 Conflict`

#### 场景：从工作区移除成员
- **当** 工作区管理员发送 DELETE `/api/orgs/{org_id}/workspaces/{ws_id}/members/{user_id}`
- **则** 系统移除成员条目并返回 `204`

### 需求：基于角色的权限强制
系统应根据用户角色强制实施工作区级权限。权限检查应作为 FastAPI 依赖函数实现，可应用于任何端点。

#### 场景：管理员管理工作区设置
- **当** 具有 `admin` 角色的用户发送 PATCH `/api/orgs/{org_id}/workspaces/{ws_id}` 进行设置更新
- **则** 系统应用更新并返回 `200`

#### 场景：编辑器不能管理成员
- **当** 具有 `editor` 角色的用户发送 POST `/api/orgs/{org_id}/workspaces/{ws_id}/members`
- **则** 系统返回 `403 Forbidden`

#### 场景：查看者不能创建资源
- **当** 具有 `viewer` 角色的用户发送 POST `/api/agents` 定义新代理
- **则** 系统返回 `403 Forbidden`

#### 场景：查看者可以读取资源
- **当** 具有 `viewer` 角色的用户发送 GET `/api/agents`
- **则** 系统返回认证工作区中的代理列表

#### 场景：编辑器可以创建和编辑资源
- **当** 具有 `editor` 角色的用户发送 POST `/api/agents` 或 PATCH `/api/agents/{id}`
- **则** 系统在认证工作区中创建或更新代理

### 需求：权限依赖函数
系统应提供以下 FastAPI 依赖函数，用于 API 端点：`require_workspace_admin()`、`require_workspace_editor()`、`require_workspace_viewer()`。这些依赖应解析认证用户在当前工作区中的角色，并在角色不足时抛出 `403 Forbidden`。

#### 场景：require_workspace_admin 依赖
- **当** 端点使用 `Depends(require_workspace_admin)` 且认证用户具有 `viewer` 角色
- **则** 依赖在端点逻辑执行前抛出 `403 Forbidden`

#### 场景：require_workspace_editor 依赖允许 admin
- **当** 端点使用 `Depends(require_workspace_editor)` 且认证用户具有 `admin` 角色
- **则** 依赖通过——admin 隐式满足 editor 级别要求

#### 场景：require_workspace_viewer 依赖允许所有角色
- **当** 端点使用 `Depends(require_workspace_viewer)` 且认证用户具有任意角色（admin/editor/viewer）
- **则** 依赖通过

### 需求：角色分配与修改
工作区管理员应能更改现有成员的角色。不能从工作区中移除最后一个管理员的 admin 角色——必须始终至少有一个管理员。

#### 场景：更改成员角色
- **当** 工作区管理员发送 PATCH `/api/orgs/{org_id}/workspaces/{ws_id}/members/{user_id}`，包含 `{role: "viewer"}`
- **则** 系统将成员角色从 editor 更新为 viewer

#### 场景：不能移除最后一个管理员
- **当** 工作区管理员尝试将自己的角色更改为 `editor`，且其是工作区中唯一的管理员
- **则** 系统返回 `409 Conflict`，提示"工作区必须至少有一个管理员"

### 需求：工作区创建者是管理员
创建工作区时，创建者应自动被添加为具有 `admin` 角色的工作区成员。此成员资格是强制性的，在工作区存在期间不能移除。

#### 场景：创建者获得管理员角色
- **当** 组织所有者创建新工作区
- **则** 系统为创建者创建 WorkspaceMemberModel 条目，`role: "admin"`

### 需求：系统级 API 密钥绕过工作区 RBAC
系统级 API 密钥应绕过工作区级 RBAC 检查。无需成员资格即可访问任意工作区中的任意资源。

#### 场景：系统密钥访问任意工作区
- **当** 请求使用系统级 API 密钥进行认证
- **则** RBAC 依赖通过，无论工作区成员资格或角色如何

#### 场景：工作区密钥遵循 RBAC
- **当** 请求使用工作区级 API 密钥进行认证
- **则** 该密钥在其绑定的工作区内被视为具有 `admin` 角色，对于其他任何工作区返回 `403 Forbidden`
