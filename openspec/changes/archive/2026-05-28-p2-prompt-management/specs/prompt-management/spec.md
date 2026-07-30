## 新增需求

### 需求：创建 Prompt
系统须提供 API 端点 `POST /api/prompts`，创建带有初始版本的新 prompt。

#### 场景：成功创建
- **当** 用户发送带有 name, template 和 variables 的 POST 请求
- **则** 系统创建 PromptModel 和 PromptVersionModel（version=1），返回 201

### 需求：读取 Prompt
系统须提供 `GET /api/prompts/{id}`，返回带有当前版本的 prompt。

#### 场景：Prompt 存在
- **当** 用户使用有效的 prompt ID 发送 GET 请求
- **则** 系统返回 200 及包含 template 和 variables 的 prompt 数据

### 需求：更新 Prompt
系统须提供 `PUT /api/prompts/{id}`，更新 prompt 并创建新版本。

#### 场景：更新创建新版本
- **当** 用户发送带有更新后 template 的 PUT 请求
- **则** 系统创建版本号递增的新版本

### 需求：删除 Prompt
系统须提供 `DELETE /api/prompts/{id}`，软删除 prompt。

#### 场景：成功删除
- **当** 用户发送 DELETE 请求
- **则** 系统设置 deleted_at，返回 204

### 需求：列出 Prompts
系统须提供 `GET /api/prompts`，返回分页的 prompts。

#### 场景：带分页列出
- **当** 用户发送带有 page 和 page_size 的 GET 请求
- **则** 系统返回分页的 prompt 列表

### 需求：版本管理
系统须为 prompts 提供版本管理端点。

#### 场景：列出版本
- **当** 用户发送 GET /api/prompts/{id}/versions
- **则** 系统返回按版本号排序的所有版本

#### 场景：回滚到版本
- **当** 用户发送 POST /api/prompts/{id}/rollback/{version}
- **则** 系统使用目标版本的 template 创建新版本

### 需求：标签部署
系统须支持标签（production/staging/development）用于 prompt 部署。

#### 场景：按标签获取 prompt
- **当** 用户发送 GET /api/prompts/by-label/production
- **则** 系统返回带有"production"标签的 prompt
