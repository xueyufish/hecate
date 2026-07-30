## MODIFIED Requirements — 修改的需求

### Requirement: Async database engine with auto-commit session — 需求：具有自动提交会话的异步数据库引擎
`engine` 模块应使用工厂函数从 `DATABASE_URL` 创建异步 SQLAlchemy 引擎，该函数应用适合方言的连接池设置。PostgreSQL 和 MySQL 应使用 pool_size=20, max_overflow=10。SQLite 应不使用连接池（内存数据库使用 StaticPool，基于文件的数据库使用默认设置）。`get_db()` FastAPI 依赖应保持不变——它应在成功时自动提交，在错误时自动回滚。

#### Scenario: Successful request commits session — 场景：成功的请求提交会话
- **WHEN — 当** FastAPI 处理器使用 `get_db()` 依赖完成且无异常
- **THEN — 则** 会话应自动提交

#### Scenario: Failed request rolls back session — 场景：失败的请求回滚会话
- **WHEN — 当** FastAPI 处理器引发异常
- **THEN — 则** 会话应回滚并重新引发异常

#### Scenario: PostgreSQL engine creation — 场景：PostgreSQL 引擎创建
- **WHEN — 当** `DATABASE_URL` 以 `postgresql+asyncpg://` 开头
- **THEN — 则** 引擎应使用 `pool_size=20, max_overflow=10` 创建

#### Scenario: MySQL engine creation — 场景：MySQL 引擎创建
- **WHEN — 当** `DATABASE_URL` 以 `mysql+aiomysql://` 开头
- **THEN — 则** 引擎应使用 `pool_size=20, max_overflow=10` 创建

#### Scenario: SQLite in-memory engine creation — 场景：SQLite 内存引擎创建
- **WHEN — 当** `DATABASE_URL` 为 `sqlite+aiosqlite://`
- **THEN — 则** 引擎应使用 `connect_args={"check_same_thread": False}` 和 `poolclass=StaticPool` 创建

#### Scenario: SQLite file-based engine creation — 场景：基于文件的 SQLite 引擎创建
- **WHEN — 当** `DATABASE_URL` 以 `sqlite+aiosqlite:///` 开头（带有文件路径）
- **THEN — 则** 引擎应在不覆盖连接池设置的情况下创建

#### Scenario: Unsupported dialect — 场景：不支持的方言
- **WHEN — 当** `DATABASE_URL` 使用不支持的 scheme
- **THEN — 则** 应在导入时引发 `ValueError`，列出支持的方言
