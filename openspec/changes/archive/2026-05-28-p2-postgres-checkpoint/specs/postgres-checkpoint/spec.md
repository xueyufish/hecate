## 新增需求

### 需求：将 checkpoint 保存到 PostgreSQL
系统须使用现有的 CheckpointModel ORM 将 checkpoint 持久化到 PostgreSQL。

#### 场景：保存 checkpoint
- **当** 使用 session_id, superstep, node_id, channel_state 调用 `save()`
- **则** 系统创建 CheckpointModel 记录并返回 checkpoint ID

#### 场景：带元数据保存
- **当** 使用元数据（例如中断信息）调用 `save()`
- **则** 元数据须存储在 metadata JSONB 列中

### 需求：从 PostgreSQL 加载 checkpoint
系统须从 PostgreSQL 加载 checkpoint，支持最新和特定 checkpoint 的检索。

#### 场景：加载最新 checkpoint
- **当** 不带 checkpoint_id 调用 `load(session_id)`
- **则** 系统返回该 session 中 superstep 最高的 checkpoint

#### 场景：加载特定 checkpoint
- **当** 调用 `load(session_id, checkpoint_id)`
- **则** 系统返回与该 ID 完全匹配的 checkpoint

#### 场景：未找到 checkpoint
- **当** 为没有 checkpoint 的 session 调用 `load()`
- **则** 系统返回 None

### 需求：列出 checkpoint
系统须按 superstep 降序列出 session 的 checkpoint。

#### 场景：带 limit 列出
- **当** 调用 `list_checkpoints(session_id, limit=10)`
- **则** 系统返回 10 个最近的 checkpoint

### 需求：热路径的内存缓存
系统须在内存中缓存每个 session 最近的 checkpoint。

#### 场景：加载时缓存命中
- **当** 为最近保存过的 session 调用 `load()`
- **则** 系统返回缓存的 checkpoint，无需查询数据库

#### 场景：保存时缓存失效
- **当** 为某个 session 调用 `save()`
- **则** 该 session 的缓存须用新 checkpoint 更新
