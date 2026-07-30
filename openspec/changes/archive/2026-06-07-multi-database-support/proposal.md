## Why — 为什么

Hecate 目前将 PostgreSQL 硬编码为唯一的数据库后端。10 个模型定义和 10 个迁移文件中的 `postgresql_where=` 部分索引使得代码库在 DDL 层面与 MySQL 和 SQLite 不兼容。与此同时，测试套件已经在 SQLite 上运行（通过 `sqlite+aiosqlite://`）——证明 ORM 模型几乎可以移植。移除 PostgreSQL 特定的假设可以解锁部署时的数据库选择（PostgreSQL / MySQL / SQLite），这是多租户 RBAC（Sprint 4）和企业部署灵活性的先决条件。

此外，当前的 `BaseModel` 在 `deleted_at` 列中混淆了两种不同的语义：它既作为删除标志（`IS NULL` = 活跃）又作为审计时间戳（何时被删除）。这应分离为用于状态的 `deleted: bool` 字段和用于审计的 `deleted_at: datetime`。

## What Changes — 变更内容

- **向 `BaseModel` 添加 `deleted: bool` 字段** — 显式的删除状态标志，默认为 `False`。`deleted_at` 保留为审计时间戳。
- **将所有 `postgresql_where=` 部分索引替换为可移植的复合索引** — 例如 `Index("idx_name", "name", "deleted")` 在 PostgreSQL、MySQL 和 SQLite 上工作方式相同。
- **更新服务层查询** — 将所有 17 个服务中的 `WHERE deleted_at IS NULL` 改为 `WHERE deleted = false`。
- **重构 `database.py` 以支持多方言** — 在启动时从 `DATABASE_URL` 检测数据库方言；使用适合方言的连接池设置创建引擎。
- **添加 Alembic 数据迁移** — 从现有 `deleted_at` 值回填 `deleted` 列（NULL → False，非 NULL → True）。
- **添加 CI 测试矩阵** — 针对 SQLite（现有）和 PostgreSQL 运行 pytest 以捕获方言回归。

## Capabilities — 能力

### New Capabilities — 新能力
- `multi-database`：部署时数据库后端选择（PostgreSQL、MySQL、SQLite），自动方言检测和可移植的 schema 定义

### Modified Capabilities — 修改的能力
- `data-models`：`BaseModel` 新增 `deleted: bool` 字段；软删除语义从 `deleted_at IS NULL` 变为 `deleted = false`；所有部分索引替换为复合索引
- `core-infrastructure`：`database.py` 支持多方言引擎创建；`DATABASE_URL` 默认值从仅 PostgreSQL 改为方言无关；`Settings` 新增数据库类型验证

## Impact — 影响

- **模型**：所有 16 个 `BaseModel` 子类新增 `deleted` 列（需要迁移）
- **索引**：跨 7 个模型文件的 10 个 `postgresql_where=` 索引替换为可移植的复合索引
- **服务**：17 个服务文件的查询过滤器从 `deleted_at IS NULL` 更新为 `deleted = false`
- **迁移**：新的 Alembic 迁移，添加 `deleted` 列、回填数据并重新创建索引
- **测试**：现有的 SQLite 测试继续通过；新增 PostgreSQL 集成测试配置
- **API**：无 API 级别变更——软删除行为对 API 消费者透明
- **依赖**：`aiomysql` 作为可选的 MySQL 支持依赖添加（`pyproject.toml` 中新增 `[mysql]` extra）
