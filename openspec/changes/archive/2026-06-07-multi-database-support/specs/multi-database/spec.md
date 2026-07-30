## ADDED Requirements — 新增的需求

### Requirement: Deploy-time database backend selection — 需求：部署时数据库后端选择
系统应支持 PostgreSQL、MySQL 和 SQLite 作为数据库后端，在部署时通过 `DATABASE_URL` 环境变量选择。数据库方言应从 URL scheme 自动检测。

#### Scenario: PostgreSQL backend — 场景：PostgreSQL 后端
- **WHEN — 当** `DATABASE_URL` 设置为 `postgresql+asyncpg://user:pass@host:5432/db`
- **THEN — 则** 系统应创建带有连接池的异步引擎（pool_size=20, max_overflow=10）

#### Scenario: MySQL backend — 场景：MySQL 后端
- **WHEN — 当** `DATABASE_URL` 设置为 `mysql+aiomysql://user:pass@host:3306/db`
- **THEN — 则** 系统应创建带有连接池的异步引擎（pool_size=20, max_overflow=10）

#### Scenario: SQLite backend — 场景：SQLite 后端
- **WHEN — 当** `DATABASE_URL` 设置为 `sqlite+aiosqlite:///path/to/db.sqlite3` 或 `sqlite+aiosqlite://`（内存）
- **THEN — 则** 系统应创建不带连接池的异步引擎（内存数据库使用 poolclass=StaticPool，基于文件的数据库不使用连接池选项）

#### Scenario: Unsupported database URL — 场景：不支持的数据库 URL
- **WHEN — 当** `DATABASE_URL` 使用不支持的 scheme（例如 `oracle+cx_oracle://`）
- **THEN — 则** 系统应在启动时引发 `ValueError`，并附带列出支持后端的消息

### Requirement: Portable schema definitions — 需求：可移植的 schema 定义
所有 ORM 模型定义和 Alembic 迁移应仅使用跨数据库兼容的 SQLAlchemy 类型和索引定义。模型或迁移代码中不应出现 `postgresql_where=`、`postgresql_using=` 或其他方言特定的索引 kwargs。

#### Scenario: No postgresql_where in models — 场景：模型中无 postgresql_where
- **WHEN — 当** 扫描代码库查找 `postgresql_where`
- **THEN — 则** `src/hecate/models/` 中应存在零匹配

#### Scenario: No postgresql_where in migrations — 场景：迁移中无 postgresql_where
- **WHEN — 当** 扫描代码库查找 `postgresql_where`
- **THEN — 则** `alembic/versions/` 中应存在零匹配

#### Scenario: func.now() used for timestamps — 场景：使用 func.now() 作为时间戳
- **WHEN — 当** 定义 `created_at` 或 `updated_at` 列
- **THEN — 则** 应使用 `server_default=func.now()`（SQLAlchemy 自动适配 SQLite 的 CURRENT_TIMESTAMP）

### Requirement: MySQL optional dependency — 需求：MySQL 可选依赖
MySQL 支持应通过 `pyproject.toml` 中的可选 `[mysql]` 依赖组提供。

#### Scenario: MySQL driver not installed — 场景：MySQL 驱动程序未安装
- **WHEN — 当** `DATABASE_URL` 为 `mysql+aiomysql://...` 且 `aiomysql` 未安装
- **THEN — 则** 系统应引发明确的导入错误，指示必须安装 `[mysql]` extra

#### Scenario: MySQL driver installed — 场景：MySQL 驱动程序已安装
- **WHEN — 当** 安装了 `hecate[mysql]` 且 `DATABASE_URL` 为 `mysql+aiomysql://...`
- **THEN — 则** 系统应正常连接和运行
