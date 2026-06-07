## 1. Model Layer — Add `deleted` field

- [x] 1.1 Add `deleted: Mapped[bool]` field to `BaseModel` in `src/hecate/models/base.py` with `default=False, server_default=False`
- [x] 1.2 Add `deleted: bool` field to all Pydantic `ReadSchema` classes that currently expose `deleted_at`

## 2. Model Layer — Replace partial indexes

- [x] 2.1 Replace `postgresql_where=` index in `agent.py` with composite `Index("idx_agents_workspace", "workspace_id", "deleted")`
- [x] 2.2 Replace `postgresql_where=` index in `workflow.py` with composite `Index("idx_workflows_workspace", "workspace_id", "deleted")`
- [x] 2.3 Replace all 4 `postgresql_where=` indexes in `model_provider.py` with composite indexes
- [x] 2.4 Replace `postgresql_where=` index in `tool.py` with composite `Index("idx_tools_workspace_name", "workspace_id", "name", "deleted", "deleted_at", unique=True)`
- [x] 2.5 Replace `postgresql_where=` index in `document.py` with composite `Index("idx_documents_kb", "knowledge_base_id", "deleted")`
- [x] 2.6 Replace `postgresql_where=` index in `conversation.py` with composite `Index("idx_conversations_agent", "agent_id", "deleted")`
- [x] 2.7 Replace `postgresql_where=` indexes in `skill.py` with composite `Index("idx_skills_name", "workspace_id", "name", "deleted", "deleted_at", unique=True)`

## 3. Service Layer — Update query filters

- [x] 3.1 Find all `deleted_at.is_(None)` / `deleted_at == None` patterns in services and replace with `~deleted`
- [x] 3.2 Find all soft-delete operations that set `deleted_at` and update them to also set `deleted = True`

## 4. Database — Multi-dialect engine factory

- [x] 4.1 Refactor `src/hecate/core/database.py` — extract `create_engine_from_url()` factory function with dialect-specific pool config
- [x] 4.2 Add SQLite pool config (StaticPool for in-memory, no pool overrides for file-based)
- [x] 4.3 Add MySQL pool config (pool_size=20, max_overflow=10)
- [x] 4.4 Add `DATABASE_URL` validation with clear error for unsupported dialects

## 5. Dependencies

- [x] 5.1 Add `[mysql]` optional dependency group to `pyproject.toml` with `aiomysql` package
- [x] 5.2 Add conditional import guard for `aiomysql` in `database.py`

## 6. Migration

- [x] 6.1 Create Alembic migration that adds `deleted` boolean column (server_default=False) to all BaseModel tables
- [x] 6.2 Add data migration step: backfill `deleted` from `deleted_at` (NULL → False, non-NULL → True)
- [x] 6.3 Drop old `postgresql_where=` partial indexes
- [x] 6.4 Create new composite indexes

## 7. Tests

- [x] 7.1 Verify all 1199 existing tests pass with SQLite (no regressions from model changes)
- [x] 7.2 Add test for `create_engine_from_url()` with each supported dialect URL
- [x] 7.3 Add test for unsupported dialect URL raising ValueError
- [x] 7.4 Add test verifying composite indexes exist and partial indexes are gone
- [x] 7.5 Verify ruff check, ruff format, mypy all pass
