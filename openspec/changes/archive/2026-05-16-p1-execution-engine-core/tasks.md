## 1. 项目骨架 — Project Skeleton

- [x] 1.1 创建 `pyproject.toml`，声明 Python 3.12+ 和核心依赖（FastAPI, Pydantic v2, SQLAlchemy 2.0 async, LiteLLM, qdrant-client, llm-guard, nemoguardrails, docling, FlagEmbedding）
- [x] 1.2 创建 `src/hecate/` 目录结构和 `__init__.py` 包文件
- [x] 1.3 创建 `src/hecate/core/config.py` 配置模块（Pydantic Settings，支持环境变量和 `.env`）
- [x] 1.4 创建 `src/hecate/core/database.py` 数据库连接模块（SQLAlchemy async engine + session factory）
- [x] 1.5 创建 `docker/docker-compose.yml`（PostgreSQL 16 + Qdrant + MinIO + Hecate API 服务）
- [x] 1.6 创建 `Dockerfile`（Python 3.12 slim 基础镜像，多阶段构建）
- [x] 1.7 创建 `.env.example` 模板文件，列出所有配置项
- [x] 1.8 创建 `tests/` 目录和 `conftest.py`（pytest-asyncio + 测试数据库 fixture）
- [x] 1.1 Create `pyproject.toml` with Python 3.12+ and core dependencies
- [x] 1.2 Create `src/hecate/` directory structure and package files
- [x] 1.3 Create `src/hecate/core/config.py` config module
- [x] 1.4 Create `src/hecate/core/database.py` database connection module
- [x] 1.5 Create `docker/docker-compose.yml` (PostgreSQL 16 + Qdrant + MinIO + Hecate API)
- [x] 1.6 Create `Dockerfile` (Python 3.12 slim, multi-stage build)
- [x] 1.7 Create `.env.example` template
- [x] 1.8 Create `tests/` directory and `conftest.py`

## 2. 数据模型 — Data Models

- [x] 2.1 创建 `src/hecate/models/base.py`：BaseModel mixin（UUID 主键、created_at、updated_at、deleted_at 软删除）
- [x] 2.2 创建 `src/hecate/models/agent.py`：Agent ORM 模型 + Pydantic schema（Create/Update/Response）
- [x] 2.3 创建 `src/hecate/models/session.py`：Session ORM 模型 + Pydantic schema
- [x] 2.4 创建 `src/hecate/models/message.py`：Message ORM 模型 + Pydantic schema（支持 tool_calls JSONB）
- [x] 2.5 创建 `src/hecate/models/tool.py`：Tool ORM 模型 + Pydantic schema（source: builtin/custom/mcp）
- [x] 2.6 创建 `src/hecate/models/knowledge.py`：KnowledgeBase ORM 模型 + Pydantic schema
- [x] 2.7 创建 `src/hecate/models/skill.py`：Skill ORM 模型 + Pydantic schema
- [x] 2.8 创建 `src/hecate/models/conversation.py`：Conversation ORM 模型 + Pydantic schema
- [x] 2.9 创建 `src/hecate/models/document.py`：Document ORM 模型 + Pydantic schema
- [x] 2.10 创建 `src/hecate/models/checkpoint.py`：Checkpoint ORM 模型 + Pydantic schema
- [x] 2.11 创建 Alembic 初始迁移脚本（9 张核心表）
- [x] 2.12 编写数据模型单元测试
- [x] 2.1-2.12 Create all 9 ORM models, Pydantic schemas, Alembic migration, and unit tests

## 3. Graph DSL + 编译器 — Graph DSL + Compiler

- [x] 3.1 创建 `src/hecate/engine/types.py`：核心类型定义
- [x] 3.2 创建 Graph DSL JSON Schema 定义文件
- [x] 3.3 实现 `src/hecate/engine/graph_dsl.py`：JSON 解析 + 验证
- [x] 3.4 实现 `src/hecate/engine/compiler.py`：Graph 编译器（JSON → CompiledGraph）
- [x] 3.5 实现三层 Agent 预设模板生成器（Guard→Plan→Sub-Agent）
- [x] 3.6 编译器错误处理和友好错误消息
- [x] 3.7 编写 Graph DSL + 编译器单元测试
- [x] 3.1-3.7 Implement Graph DSL JSON Schema, compiler, template generator, error handling, and tests

## 4. 执行引擎 — Execution Engine

- [x] 4.1 实现 `src/hecate/engine/channel.py`：Channel 状态管理
- [x] 4.2 实现 `src/hecate/engine/checkpoint.py`：Checkpoint 持久化接口 + PostgreSQL 实现
- [x] 4.3 实现 `src/hecate/engine/worker.py`：Worker 接口 + P1 线程池实现
- [x] 4.4 实现 `src/hecate/engine/pregel.py`：Pregel 运行时（superstep 循环）
- [x] 4.5 实现 interrupt/恢复机制
- [x] 4.6 实现子图执行
- [x] 4.7 实现 `src/hecate/engine/ports.py`：EnginePort 接口
- [x] 4.8 编写执行引擎集成测试
- [x] 4.1-4.8 Implement Channel, Checkpoint, Worker Pool, Pregel runtime, interrupt/resume, subgraph, EnginePort, and integration tests

## 5. API 层 — API Layer

- [x] 5.1 创建 `src/hecate/main.py`：FastAPI 应用初始化
- [x] 5.2 创建 `src/hecate/core/deps.py`：通用依赖注入
- [x] 5.3 实现 `/api/agents` CRUD
- [x] 5.4 实现 `/api/sessions`
- [x] 5.5 实现 `/api/tools`
- [x] 5.6 实现 `/api/skills`
- [x] 5.7 实现 `/api/knowledge-bases`
- [x] 5.8 实现 `/v1/chat/completions`（OpenAI 兼容）
- [x] 5.9 实现 `/v1/models`
- [x] 5.10 实现 SSE streaming 响应格式
- [x] 5.11 实现 Rate Limiting
- [x] 5.12 编写 API 集成测试
- [x] 5.13 实现 `/api/conversations` 端点

## 6. LLM 模型路由 — LLM Model Routing

- [x] 6.1 实现 `src/hecate/services/llm/service.py`：LiteLLM 封装
- [x] 6.2 实现 streaming 响应生成器
- [x] 6.3 实现 tool calling 协议
- [x] 6.4 实现模型降级策略
- [x] 6.5 实现 `/v1/models` 模型列表
- [x] 6.6 编写 LLM 服务测试

## 7. RAG 管线 — RAG Pipeline

- [x] 7.1 实现 `src/hecate/services/rag/embedding.py`：BGE-M3 封装
- [x] 7.2 实现 `src/hecate/services/rag/parser.py`：Docling 文档解析
- [x] 7.3 实现 `src/hecate/services/rag/chunker.py`：文本分片
- [x] 7.4 实现 `src/hecate/services/rag/indexer.py`：Qdrant 索引管理
- [x] 7.5 实现 `src/hecate/services/rag/searcher.py`：Hybrid Search
- [x] 7.6 实现 Knowledge Base 服务
- [x] 7.7 实现 `src/hecate/services/rag/storage.py`：MinIO 集成
- [x] 7.8 实现 documents 表状态追踪
- [x] 7.9 编写 RAG 管线测试

## 8. 安全层 — Security Layer

- [x] 8.1 实现 `src/hecate/services/security/llm_guard.py`：LLM Guard Scanner 封装
- [x] 8.2 实现 Anonymize/Deanonymize Vault 管理
- [x] 8.3 实现 NeMo Guardrails 基础配置
- [x] 8.4 实现安全中间件
- [x] 8.5 实现 API Key 认证中间件
- [x] 8.6 编写安全层测试

## 9. MCP 集成 — MCP Integration

- [x] 9.1 实现 `src/hecate/services/mcp/client.py`：MCP Client 连接管理
- [x] 9.2 实现 MCP Tool 同步
- [x] 9.3 实现 MCP Tool 调用
- [x] 9.4 编写 MCP 集成测试

## 10. 端到端集成 — End-to-End Integration

- [x] 10.1 实现三层 Agent 模板到 Graph 的完整编译
- [x] 10.2 实现完整对话闭环
- [x] 10.3 实现 tool calling 完整流程
- [x] 10.4 实现 RAG 检索集成
- [x] 10.5-10.10 编写端到端集成测试（对话闭环、tool calling、RAG、interrupt/resume、model fallback、Docker Compose 冒烟测试）
- [x] 10.1-10.10 Implement and test complete Agent conversation loop, tool calling, RAG retrieval, interrupt/resume, model fallback, Docker Compose smoke test

## 11. 文档和收尾 — Documentation and Wrap-up

- [x] 11.1 编写 README.md
- [x] 11.2 更新 `AGENTS.md`
- [x] 11.3 配置 CI（GitHub Actions）
- [x] 11.4 配置 pre-commit hooks
- [x] 11.5 自定义 FastAPI OpenAPI spec
- [x] 11.1-11.5 Write README, update AGENTS.md, configure CI, pre-commit hooks, customize OpenAPI spec
