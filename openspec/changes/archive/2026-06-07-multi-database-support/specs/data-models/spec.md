## MODIFIED Requirements — 修改的需求

### Requirement: BaseModel provides UUID primary key, timestamps, and soft delete — 需求：BaseModel 提供 UUID 主键、时间戳和软删除
抽象的 `BaseModel` 应为所有具体的 ORM 模型提供 `id`（UUID4）、`created_at`、`updated_at`、`deleted`（布尔值）和 `deleted_at` 列。`deleted` 字段表示删除状态；`deleted_at` 字段是记录删除发生时间的审计时间戳。

#### Scenario: UUID primary key auto-generated — 场景：UUID 主键自动生成
- **WHEN — 当** 创建新的模型实例
- **THEN — 则** `id` 应通过 `uuid.uuid4` 自动生成

#### Scenario: Timestamps set by database server — 场景：由数据库服务器设置时间戳
- **WHEN — 当** 插入一行
- **THEN — 则** `created_at` 和 `updated_at` 应由 `server_default=func.now()` 设置

#### Scenario: Updated_at refreshed on UPDATE — 场景：UPDATE 时刷新 Updated_at
- **WHEN — 当** 更新一行
- **THEN — 则** `updated_at` 应通过 `onupdate=func.now()` 刷新

#### Scenario: New row is not deleted by default — 场景：默认情况下新行未删除
- **WHEN — 当** 创建新的模型实例
- **THEN — 则** `deleted` 应为 `False`，`deleted_at` 应为 `None`

#### Scenario: Soft delete sets both deleted and deleted_at — 场景：软删除同时设置 deleted 和 deleted_at
- **WHEN — 当** 软删除一行
- **THEN — 则** `deleted` 应设置为 `True`，`deleted_at` 应设置为当前时间戳

#### Scenario: Active rows queried by deleted field — 场景：通过 deleted 字段查询活跃行
- **WHEN — 当** 查询过滤活跃（未删除）行
- **THEN — 则** 应使用 `WHERE deleted = false`（而非 `WHERE deleted_at IS NULL`）

#### Scenario: Unique composite indexes include deleted field — 场景：唯一复合索引包含 deleted 字段
- **WHEN — 当** 唯一索引强制活跃行的名称唯一性
- **THEN — 则** 索引应为 `Index("name", <columns...>, "deleted", "deleted_at", unique=True)`——在 PostgreSQL、MySQL 和 SQLite 上完全可移植

#### Scenario: Non-unique filtered indexes include deleted field — 场景：非唯一过滤索引包含 deleted 字段
- **WHEN — 当** 非唯一索引之前使用了 `postgresql_where=deleted_at IS NULL`
- **THEN — 则** 索引应为 `Index("name", <columns...>, "deleted")`——无方言特定 kwargs 的复合索引
