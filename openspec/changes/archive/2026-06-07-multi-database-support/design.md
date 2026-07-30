## Context — 上下文

Hecate 使用单个 `DATABASE_URL`（默认为 `postgresql+asyncpg://`），在 `core/config.py` 中配置。异步引擎和会话工厂在 `core/database.py` 中的模块导入时创建一次。所有 16 个 ORM 模型继承自 `BaseModel`（定义在 `models/base.py`），它提供 `id`、`created_at`、`updated_at` 和 `deleted_at` 列。

当前的软删除模式使用 `deleted_at IS NULL` 作为删除标志和审计时间戳。这迫使 10 个部分索引使用 PostgreSQL 特定的 `postgresql_where=` 语法。测试套件通过 `conftest.py` 使用 `sqlite+aiosqlite://` 在 SQLite 上运行，确认 ORM 层已经基本可移植——部分索引在 SQLite 上被静默忽略。

17 个服务通过 FastAPI 的 `Depends(get_db)` 注入 `AsyncSession`，并在查询中应用 `WHERE deleted_at IS NULL` 过滤器。

## Goals / Non-Goals — 目标 / 非目标

**Goals — 目标：**
- 支持 PostgreSQL、MySQL 和 SQLite 作为部署时的数据库选择（每个部署一个后端）
- 将删除状态（`deleted: bool`）与审计时间戳（`deleted_at: datetime`）分离
- 将所有 `postgresql_where=` 部分索引替换为可移植的复合索引
- 所有现有测试在 SQLite 上继续通过；新增 PostgreSQL CI 验证
- 零 API 级别破坏性变更——软删除行为对 API 消费者保持透明

**Non-Goals — 非目标：**
- 同时多数据库连接（分片、读副本）
- 除 Alembic 外的自动数据库配置或 schema 管理
- Oracle、SQL Server 或其他数据库后端
- 引擎层变更——这纯粹是 `core/` + `models/` + `services/` 的变更

## Decisions — 决策

### D1: `deleted: bool` 字段替代重用 `deleted_at` 作为状态

**Decision — 决策**：向 `BaseModel` 添加 `deleted: Mapped[bool]`，`default=False`。保留 `deleted_at` 作为纯审计时间戳。

**Rationale — 理由**：`deleted` 字段表示"当前状态"（此行是否被删除？），而 `deleted_at` 表示"删除发生的时间"（审计）。这是不同的关注点。布尔字段可以创建干净的复合索引，如 `Index("idx_name", "name", "deleted")`，在 PostgreSQL、MySQL 和 SQLite 上工作方式相同。

**Alternatives considered — 考虑的替代方案**：
- `CHAR(1)` 使用 N/Y 值——更可读但增加了字符串比较开销，失去了 SQLAlchemy `Boolean` 类型安全性
- `deleted_at` 使用哨兵值（纪元 0 替代 NULL）——改变了 NULL 语义，不够直观

### D2: 复合索引 `(column, deleted)` 替代方言感知的部分索引

**Decision — 决策**：将所有 `Index("name", col, postgresql_where=BaseModel.deleted_at.is_(None))` 替换为 `Index("name", col, "deleted")`。

**Rationale — 理由**：`(col, deleted)` 上的复合索引在所有三个数据库中产生相同的唯一性语义。由于 `deleted` 是布尔值，索引基数很低（2 个值），组合索引效率高。不需要运行时方言检测。

**What this changes — 变更内容**：
```
Before — 之前: Index("idx_agents_workspace", "workspace_id", postgresql_where=deleted_at IS NULL)
After — 之后:  Index("idx_agents_workspace", "workspace_id", "deleted")
```
对于唯一索引，`(name, deleted)` 允许最多一个具有相同 `name` 且 `deleted=False` 的活跃行，以及任意数量的已删除行（`deleted=True`）。由于多个具有相同名称的已删除行可以共存，复合索引提供了与部分索引相同的实际唯一性保证。

**Edge case — 边界情况**：两个具有相同 `(name, deleted=True)` 的已删除行会违反唯一复合索引。解决方案是对于大多数情况使用**非唯一**复合索引，仅当业务规则要求时才将 `deleted` 添加到唯一索引中。对于诸如每个工作空间的工具名称之类的唯一约束，索引变为 `Index("name", "workspace_id", "deleted", unique=True)`——这意味着只允许一个 `(workspace_id, name, deleted=False)` 行，而已删除行使用其 `deleted_at` 时间戳进行区分（但仅 `deleted` 无法区分两个名称相同的已删除行）。

**Resolution — 解决方案**：对于唯一索引，追加 `deleted_at` 使元组完全唯一：
```
Index("name", "workspace_id", "deleted", "deleted_at", unique=True)
```
这之所以有效，是因为活跃行的 `deleted_at` 为 NULL（所有活跃行共享相同的 NULL），而已删除行具有唯一的时间戳。活跃唯一性由 `(workspace_id, name, False, NULL)` 保证，而已删除行通过其时间戳区分。

等等——唯一索引中的 NULL 值在不同数据库中处理方式不同。PostgreSQL 允许唯一索引中的多个 NULL，MySQL 允许（自 5.1 起），SQLite 也允许多个 NULL。因此，`(workspace_id, name, deleted, deleted_at)` 对于活跃行使用 `deleted_at=NULL`，对于已删除行使用唯一时间戳，实际上是**可移植且正确的**。

### D3: 从 DATABASE_URL 检测方言

**Decision — 决策**：从 `DATABASE_URL` 字符串解析数据库驱动程序以确定方言。没有单独的 `DB_TYPE` 配置变量。

**Rationale — 理由**：URL scheme 已经编码了方言（`postgresql+asyncpg`、`mysql+aiomysql`、`sqlite+aiosqlite`）。单独的配置变量将是冗余的，并可能不同步。

### D4: 引擎创建重构为工厂函数

**Decision — 决策**：将模块级别的 `engine = create_async_engine(settings.DATABASE_URL, ...)` 替换为 `create_engine_from_url()` 函数，该函数应用方言特定的默认值（PG/MySQL 的连接池配置，SQLite 无连接池）。

**Rationale — 理由**：SQLite 不支持连接池（内存数据库恰好有一个连接）。PostgreSQL 和 MySQL 受益于 `pool_size`/`max_overflow`。工厂函数封装了这种变化。

### D5: 迁移策略——增量迁移

**Decision — 决策**：添加单个新的 Alembic 迁移，该迁移（1）添加 `server_default="0"` 的 `deleted` 列，（2）从 `deleted_at` 回填，（3）删除旧的部分索引，（4）创建新的复合索引。

**Rationale — 理由**：重写迁移链会破坏现有部署。增量迁移保留了完整的历史记录，并允许零停机升级。

## Risks / Trade-offs — 风险 / 权衡

- **复合索引略大于部分索引** → `deleted` 布尔值每个索引条目增加 1 字节。对于 Hecate 的数据量来说可以忽略不计。
- **带有 `deleted_at` NULL 的唯一复合索引** → 所有三个数据库（PG、MySQL、SQLite）将唯一索引中的 NULL 视为不同，因此多个具有相同 `(name, workspace_id, False, NULL)` 的活跃行不会违反唯一性。然而，`(name, workspace_id, deleted=False)` 的唯一性已经由 `deleted` 布尔值强制执行——每个名称只能有一个活跃行。`deleted_at` 列仅用于区分已删除行。→ **缓解措施**：对于非唯一索引（大多数情况），仅 `deleted` 就足够了。对于唯一索引，使用 `(columns..., deleted, deleted_at)` 复合索引。
- **现有部署需要迁移** → 部署新代码之前必须运行 Alembic 迁移。旧代码会忽略新的 `deleted` 列。→ **缓解措施**：使用 `server_default=False` 添加 `deleted`，以便旧行立即可用。
- **服务层变更广泛（17 个文件）** → 机械的查找替换，但必须彻底。→ **缓解措施**：使用基于 AST 的搜索查找所有 `deleted_at.is_(None)` 和 `deleted_at == None` 模式。
