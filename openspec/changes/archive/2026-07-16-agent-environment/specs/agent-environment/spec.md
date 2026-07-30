## ADDED Requirements — 新增需求

### 需求：AgentEnvironment 抽象

系统应提供一个 `AgentEnvironment` ABC，表示 Agent 的持久执行环境。每个环境作用于单个 Agent，包含会话、文件、内存和技能的子目录。

#### 场景：环境包含必需的子目录
- **当** 创建 Agent 环境时
- **则** 环境包含 `sessions/`、`files/`、`memory/` 和 `skills/` 子目录

#### 场景：环境作用于单个 Agent
- **当** 访问 Agent A 的环境时
- **则** Agent A 无法访问 Agent B 的环境文件

### 需求：LocalEnvironment 文件系统实现

系统应提供一个 `LocalEnvironment` 实现，将 Agent 数据存储在本地文件系统的 `{WORKSPACE_ROOT}/{agent_id}/`。

#### 场景：文件写入和读取
- **当** 文件写入环境的 `files/report.txt` 时
- **则** 该文件可以以相同内容被读取回来

#### 场景：文件列表
- **当** 文件存在于 `files/` 子目录中时
- **则** `list_files("files/")` 返回文件列表及元数据

#### 场景：文件删除
- **当** 文件从环境中被删除时
- **则** 后续的 `exists()` 返回 False

### 需求：EnvironmentManager 生命周期

系统应提供一个 `EnvironmentManager`，使用懒创建和基于 TTL 的驱逐来管理环境生命周期。

#### 场景：首次使用懒创建
- **当** 为没有现有环境的 Agent 调用 `get_environment(agent_id)` 时
- **则** 创建并返回一个新环境

#### 场景：缓存环境复用
- **当** 为同一 Agent 调用两次 `get_environment(agent_id)` 时
- **则** 返回同一环境实例（缓存）

#### 场景：TTL 驱逐
- **当** 环境空闲时间超过配置的 TTL 时
- **则** 下次访问时环境被关闭并从缓存中移除

#### 场景：交互时 TTL 重置
- **当** 对环境执行文件操作时
- **则** 环境的 TTL 计时器被重置

#### 场景：关闭所有环境
- **当** 调用 `close_all()`（例如应用关闭时）
- **则** 所有缓存的环境被关闭

### 需求：文件管理 REST API

系统应暴露用于管理 Agent 环境中文件的 REST API 端点。

#### 场景：列出文件
- **当** 客户端请求 `GET /api/agents/{agent_id}/environment/files` 时
- **则** 系统返回 `files/` 子目录中的文件列表

#### 场景：读取文件
- **当** 客户端请求 `GET /api/agents/{agent_id}/environment/files/{path}` 时
- **则** 系统返回文件内容

#### 场景：写入文件
- **当** 客户端请求使用文件内容的 `POST /api/agents/{agent_id}/environment/files` 时
- **则** 文件被写入 `files/` 子目录

#### 场景：删除文件
- **当** 客户端请求 `DELETE /api/agents/{agent_id}/environment/files/{path}` 时
- **则** 文件从环境中被移除

#### 场景：环境统计
- **当** 客户端请求 `GET /api/agents/{agent_id}/environment/stats` 时
- **则** 系统返回文件数量、总大小和创建时间

### 需求：会话自动关联

系统应自动将会话与 Agent 的环境关联。无需手动管理环境 ID。

#### 场景：会话获取环境上下文
- **当** 为 Agent 创建会话时
- **则** Agent 的环境信息（根路径）在执行上下文中可用

#### 场景：多个会话共享环境
- **当** 为同一 Agent 创建两个会话时
- **则** 两个会话访问相同的环境文件
