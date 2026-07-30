## MODIFIED Requirements — 修改的需求

### 需求：BaseModel 提供 UUID 主键、时间戳和软删除
抽象 `BaseModel` 应为所有具体 ORM 模型提供 `id`（UUID4）、`created_at`、`updated_at`、`deleted`（布尔值）和 `deleted_at` 列。`deleted` 字段表示删除状态；`deleted_at` 字段是记录删除时间的审计时间戳

#### 场景：UUID 主键自动生成
- **当** 创建新模型实例
- **则** `id` 应通过 `uuid.uuid4` 自动生成

#### 场景：时间戳由数据库服务器设置
- **当** 插入行
- **则** `created_at` 和 `updated_at` 应通过 `server_default=func.now()` 设置

#### 场景：更新时 refreshed_at 刷新
- **当** 更新行
- **则** `updated_at` 应通过 `onupdate=func.now()` 刷新

#### 场景：新行默认未删除
- **当** 创建新模型实例
- **则** `deleted` 应为 `False`，`deleted_at` 应为 `None`

#### 场景：软删除同时设置 deleted 和 deleted_at
- **当** 软删除行
- **则** `deleted` 应设置为 `True`，`deleted_at` 应设置为当前时间戳

#### 场景：按 deleted 字段查询活动行
- **当** 查询过滤活动（未删除）行
- **则** 应使用 `WHERE deleted = false`（而非 `WHERE deleted_at IS NULL`）

#### 场景：唯一复合索引包含 deleted 字段
- **当** 唯一索引在活动行中强制名称唯一性
- **则** 索引应为 `Index("name", <columns...>, "deleted", "deleted_at", unique=True)`——完全跨 PostgreSQL、MySQL 和 SQLite 可移植

#### 场景：非唯一过滤索引包含 deleted 字段
- **当** 非唯一索引以前使用 `postgresql_where=deleted_at IS NULL`
- **则** 索引应为 `Index("name", <columns...>, "deleted")`——无方言特定 kwargs 的复合索引

#### 场景：租户限定模型具有 workspace_id FK
- **当** 定义属于租户的资源模型
- **则** 它应具有指向 `WorkspaceModel.id` 的 `workspace_id` UUID 列与 FK，在 `(workspace_id, deleted)` 上的复合索引 `idx_<table>_workspace`，以及零 UUID 的服务器默认值

#### 场景：租户限定模型按 workspace_id 过滤
- **当** 对租户限定模型执行服务层查询
- **则** 查询应包括 `WHERE workspace_id = :workspace_id` 作为强制过滤条件
