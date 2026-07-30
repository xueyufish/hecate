## 1. 模型层 —— 添加 `deleted` 字段

- [x] 1.1 在 `src/hecate/models/base.py` 的 `BaseModel` 中添加 `deleted: Mapped[bool]` 字段，使用 `default=False, server_default=False`
- [x] 1.2 在所有当前暴露 `deleted_at` 的 Pydantic `ReadSchema` 类中添加 `deleted: bool` 字段

## 2. 模型层 —— 替换部分索引

- [x] 2.1 将 `agent.py` 中的 `postgresql_where=` 索引替换为复合 `Index("idx_agents_workspace", "workspace_id", "deleted")`
- [x] 2.2 将 `workflow.py` 中的 `postgresql_where=` 索引替换为复合 `Index("idx_workflows_workspace", "workspace_id", "deleted")`
- [x] 2.3 将 `model_provider.py` 中的所有 4 个 `postgresql_where=` 索引替换为复合索引
- [x] 2.4 将 `tool.py` 中的 `postgresql_where=` 索引替换为复合 `Index("idx_tools_workspace_name", "workspace_id", "name", "deleted", "deleted_at", unique=True)`
- [x] 2.5 将 `document.py` 中的 `postgresql_where=` 索引替换为复合 `Index("idx_documents_kb", "knowledge_base_id", "deleted")`
- [x] 2.6 将 `conversation.py` 中的 `postgresql_where=` 索引替换为复合 `Index("idx_conversations_agent", "agent_id", "deleted")`
- [x] 2.7 将 `skill.py` 中的 `postgresql_where=` 索引替换为复合 `Index("idx_skills_name", "workspace_id", "name", "deleted", "deleted_at", unique=True)`

## 3. 服务层 —— 更新查询过滤器

- [x] 3.1 查找服务中所有 `deleted_at.is_(None)` / `deleted_at == None` 模式并替换为 `~deleted`
- [x] 3.2 查找所有设置 `deleted_at` 的软删除操作，并更新为同时设置 `deleted = True`

## 4. 数据库 —— 多方言引擎工厂

- [x] 4.1 重构 `src/hecate/core/database.py` —— 提取 `create_engine_from_url()` 工厂函数，包含方言特定的连接池配置
- [x] 4.2 添加 SQLite 连接池配置（内存数据库使用 StaticPool，基于文件的不覆盖连接池）
- [x] 4.3 添加 MySQL 连接池配置（pool_size=20, max_overflow=10）
- [x] 4.4 添加 `DATABASE_URL` 验证，对不支持的方言给出明确的错误提示

## 5. 依赖

- [x] 5.1 向 `pyproject.toml` 添加 `[mysql]` 可选依赖组，包含 `aiomysql` 包
- [x] 5.2 在 `database.py` 中添加 `aiomysql` 的条件导入守卫

## 6. 迁移

- [x] 6.1 创建 Alembic 迁移，向所有 BaseModel 表添加 `deleted` 布尔列（server_default=False）
- [x] 6.2 添加数据迁移步骤：从 `deleted_at` 回填 `deleted`（NULL → False，非 NULL → True）
- [x] 6.3 删除旧的 `postgresql_where=` 部分索引
- [x] 6.4 创建新的复合索引

## 7. 测试

- [x] 7.1 验证所有 1199 个现有测试在 SQLite 上通过（模型变更无回归）
- [x] 7.2 为 `create_engine_from_url()` 添加测试，覆盖每种支持的方言 URL
- [x] 7.3 添加测试验证不支持的方言 URL 会引发 ValueError
- [x] 7.4 添加测试验证复合索引存在且部分索引已删除
- [x] 7.5 验证 ruff check、ruff format、mypy 全部通过
