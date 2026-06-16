## ADDED Requirements

### Requirement: Python 项目结构

项目 MUST 使用 Python `src` layout 目录结构。项目根目录 SHALL 包含以下结构：

```
hecate/
├── pyproject.toml
├── README.md
├── .env.example
├── .gitignore
├── Dockerfile
├── docker/
│   └── docker-compose.yml
├── alembic/
│   ├── alembic.ini
│   └── versions/
├── src/
│   └── hecate/
│       ├── __init__.py
│       ├── main.py              # FastAPI app 入口
│       ├── api/                 # API 路由层
│       │   ├── __init__.py
│       │   ├── v1/              # OpenAI 兼容层
│       │   └── management/      # Hecate 管理 API
│       ├── engine/              # 执行引擎
│       │   ├── __init__.py
│       │   ├── graph_dsl.py     # Graph DSL JSON Schema 定义
│       │   ├── compiler.py      # Graph 编译器
│       │   ├── channel.py       # Channel 系统
│       │   ├── pregel.py        # Pregel 运行时
│       │   ├── checkpoint.py    # Checkpoint 持久化
│       │   ├── worker.py        # Worker Pool
│       │   ├── command.py       # Command / interrupt
│       │   ├── ports.py         # EnginePort 接口
│       │   └── types.py         # 核心类型定义
│       ├── models/              # 数据模型 (Pydantic + SQLAlchemy)
│       │   ├── __init__.py
│       │   ├── base.py          # Base model + mixins
│       │   ├── agent.py
│       │   ├── conversation.py
│       │   ├── session.py
│       │   ├── message.py
│       │   ├── tool.py
│       │   ├── knowledge.py
│       │   ├── document.py
│       │   ├── skill.py
│       │   └── checkpoint.py
│       ├── services/            # 能力服务层
│       │   ├── __init__.py
│       │   ├── llm/             # LiteLLM 封装
│       │   ├── rag/             # RAG 管线
│       │   ├── security/        # 安全层
│       │   └── mcp/             # MCP 集成
│       └── core/                # 核心配置
│           ├── __init__.py
│           ├── config.py
│           ├── database.py
│           └── deps.py          # FastAPI 依赖注入
└── tests/
    ├── __init__.py
    ├── test_engine/
    ├── test_api/
    ├── test_services/
    └── conftest.py
```

#### Scenario: 项目结构完整性验证
- **WHEN** 检查项目根目录
- **THEN** MUST 存在 `pyproject.toml`、`src/hecate/`、`tests/`、`docker/docker-compose.yml` 四项核心路径

### Requirement: pyproject.toml 依赖配置

`pyproject.toml` MUST 声明项目元数据和所有 P1 依赖。Python 版本 MUST 为 `>=3.12`。核心依赖 SHALL 包含：`fastapi`、`uvicorn`、`pydantic>=2.0`、`sqlalchemy>=2.0`、`asyncpg`（PostgreSQL async driver）、`alembic`、`litellm`、`qdrant-client`、`llm-guard`、`nemoguardrails`、`docling`、`FlagEmbedding`（BGE-M3）、`httpx`、`python-dotenv`。开发依赖 SHALL 包含：`pytest`、`pytest-asyncio`、`pytest-cov`、`ruff`（linter + formatter）。

#### Scenario: pip install 成功安装所有依赖
- **WHEN** 在项目根目录执行 `pip install -e ".[dev]"`
- **THEN** MUST 成功安装所有核心依赖和开发依赖，无版本冲突错误

### Requirement: Docker Compose 部署配置

项目 MUST 提供 `docker/docker-compose.yml`，定义以下服务：`postgres`（PostgreSQL 16，端口 5432，持久化数据卷）、`qdrant`（Qdrant 1.x，端口 6333，持久化数据卷）、`minio`（MinIO，端口 9000/9001，持久化数据卷）、`hecate`（Hecate 服务，端口 8000，依赖 postgres 和 qdrant）。PostgreSQL MUST 使用自定义数据库名 `hecate` 和用户 `hecate`。所有服务 MUST 通过 `.env` 文件配置密码和密钥。

#### Scenario: docker compose up 一键启动
- **WHEN** 执行 `docker compose up -d`
- **THEN** MUST 成功启动 postgres、qdrant、minio、hecate 四个服务，hecate 服务日志显示 FastAPI 应用启动成功

#### Scenario: 数据持久化验证
- **WHEN** 写入数据后执行 `docker compose down && docker compose up -d`
- **THEN** PostgreSQL 和 Qdrant 中的数据 MUST 保留，可正常读取

### Requirement: 环境配置管理

项目 MUST 提供 `.env.example` 文件，列出所有需要配置的环境变量。环境变量 SHALL 包含：`DATABASE_URL`（PostgreSQL 连接串）、`QDRANT_URL`（Qdrant 地址）、`MINIO_URL`、`MINIO_ACCESS_KEY`、`MINIO_SECRET_KEY`、`OPENAI_API_KEY`、`ANTHROPIC_API_KEY`（可选）、`HECATE_API_KEYS`（逗号分隔的有效 API Key 列表）、`LLM_GUARD_ENABLED`（默认 true）、`RATE_LIMIT_RPM`（默认 60）。系统 MUST 通过 `python-dotenv` 加载 `.env` 文件。

#### Scenario: 从 .env 文件加载配置
- **WHEN** `.env` 文件包含 `DATABASE_URL=postgresql+asyncpg://hecate:password@localhost:5432/hecate`
- **THEN** 应用启动时 MUST 使用该连接串建立数据库连接池

#### Scenario: 缺少必需环境变量时启动失败
- **WHEN** `.env` 文件缺少 `DATABASE_URL` 配置
- **THEN** 应用 MUST 在启动时抛出明确的配置错误，提示缺少必需的环境变量

### Requirement: 数据库迁移管理

项目 MUST 使用 Alembic 管理 PostgreSQL Schema 迁移。`alembic/` 目录 SHALL 包含配置文件和迁移脚本。首次部署时 MUST 执行 `alembic upgrade head` 创建所有核心表（agents、conversations、sessions、messages、tools、knowledge_bases、documents、skills、checkpoints）。每次 Schema 变更 MUST 生成新的迁移脚本。

#### Scenario: 首次部署执行迁移创建所有表
- **WHEN** 在新数据库上执行 `alembic upgrade head`
- **THEN** MUST 创建 agents、conversations、sessions、messages、tools、knowledge_bases、documents、skills、checkpoints 九张核心表及其索引

### Requirement: 开发环境快速启动

项目 MUST 提供 README.md 中的快速启动指南，步骤包含：1) 克隆仓库，2) 复制 `.env.example` 为 `.env` 并填入配置，3) `docker compose up -d` 启动基础设施，4) `pip install -e ".[dev]"` 安装依赖，5) `alembic upgrade head` 初始化数据库，6) `python -m hecate` 或 `uvicorn src.hecate.main:app --reload` 启动服务。开发环境 MUST 支持热重载（代码修改自动生效）。

#### Scenario: 新开发者按指南完成环境搭建
- **WHEN** 新开发者按照 README.md 的 6 个步骤操作
- **THEN** MUST 能在 15 分钟内完成环境搭建，`curl http://localhost:8000/health` 返回 `{"status": "ok"}`
