## ADDED Requirements — 新增需求

### Requirement: Python 项目结构 — Python Project Structure

项目 MUST 使用 Python `src` layout 目录结构，包含 `pyproject.toml`、`src/hecate/`、`tests/`、`docker/` 等核心路径。所有子模块按层组织：api、engine、models、services、core。
— Project MUST use Python `src` layout with submodules organized by layer.

### Requirement: pyproject.toml 依赖配置 — pyproject.toml Dependencies

Python >=3.12. Core dependencies: fastapi, uvicorn, pydantic>=2.0, sqlalchemy>=2.0, asyncpg, alembic, litellm, qdrant-client, llm-guard, nemoguardrails, docling, FlagEmbedding, httpx, python-dotenv. Dev: pytest, pytest-asyncio, pytest-cov, ruff.

#### Scenario: pip install 成功安装所有依赖 — pip install succeeds
- **WHEN** 执行 `pip install -e ".[dev]"`
- **THEN** MUST 成功安装所有依赖

### Requirement: Docker Compose 部署配置 — Docker Compose Deployment

Services: postgres (16), qdrant (1.x), minio, hecate. PostgreSQL with database `hecate` and user `hecate`. All services configured via `.env`.

#### Scenario: docker compose up 一键启动 — One-command startup
- **WHEN** 执行 `docker compose up -d`
- **THEN** MUST 成功启动四个服务

### Requirement: 环境配置管理 — Environment Configuration

`.env.example` covering: DATABASE_URL, QDRANT_URL, MINIO_URL, MINIO_ACCESS/SECRET_KEY, OPENAI_API_KEY, ANTHROPIC_API_KEY, HECATE_API_KEYS, LLM_GUARD_ENABLED, RATE_LIMIT_RPM.

#### Scenario: 缺少必需环境变量时启动失败 — Fail on missing required env var
- **WHEN** `.env` 缺少 `DATABASE_URL`
- **THEN** 应用 MUST 在启动时抛出明确错误

### Requirement: 数据库迁移管理 — Database Migration

Alembic for PostgreSQL schema migration. `alembic upgrade head` creates all 9 core tables.

### Requirement: 开发环境快速启动 — Quick Start for Development

6-step guide in README: clone → .env → docker compose → pip install → alembic upgrade → uvicorn.

#### Scenario: 新开发者按指南完成环境搭建 — Developer follows guide
- **WHEN** 按 6 个步骤操作
- **THEN** 15 分钟内完成搭建，`/health` 返回 `{"status": "ok"}`
