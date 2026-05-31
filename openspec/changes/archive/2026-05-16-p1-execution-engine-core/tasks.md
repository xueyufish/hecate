## 1. 项目骨架

- [x] 1.1 创建 `pyproject.toml`，声明 Python 3.12+ 和核心依赖（FastAPI, Pydantic v2, SQLAlchemy 2.0 async, LiteLLM, qdrant-client, llm-guard, nemoguardrails, docling, FlagEmbedding）
- [x] 1.2 创建 `src/hecate/` 目录结构和 `__init__.py` 包文件
- [x] 1.3 创建 `src/hecate/core/config.py` 配置模块（Pydantic Settings，支持环境变量和 `.env`）
- [x] 1.4 创建 `src/hecate/core/database.py` 数据库连接模块（SQLAlchemy async engine + session factory）
- [x] 1.5 创建 `docker/docker-compose.yml`（PostgreSQL 16 + Qdrant + MinIO + Hecate API 服务）
- [x] 1.6 创建 `Dockerfile`（Python 3.12 slim 基础镜像，多阶段构建）
- [x] 1.7 创建 `.env.example` 模板文件，列出所有配置项
- [x] 1.8 创建 `tests/` 目录和 `conftest.py`（pytest-asyncio + 测试数据库 fixture）

## 2. 数据模型

- [x] 2.1 创建 `src/hecate/models/base.py`：BaseModel mixin（UUID 主键、created_at、updated_at、deleted_at 软删除）
- [x] 2.2 创建 `src/hecate/models/agent.py`：Agent ORM 模型 + Pydantic schema（Create/Update/Response）
- [x] 2.3 创建 `src/hecate/models/session.py`：Session ORM 模型 + Pydantic schema
- [x] 2.4 创建 `src/hecate/models/message.py`：Message ORM 模型 + Pydantic schema（支持 tool_calls JSONB）
- [x] 2.5 创建 `src/hecate/models/tool.py`：Tool ORM 模型 + Pydantic schema（source: builtin/custom/mcp，risk_level, approval_required）
- [x] 2.6 创建 `src/hecate/models/knowledge.py`：KnowledgeBase ORM 模型 + Pydantic schema（embedding_model, qdrant_collection, chunk_size）
- [x] 2.7 创建 `src/hecate/models/skill.py`：Skill ORM 模型 + Pydantic schema（source: system/user/project，path）
- [x] 2.8 创建 `src/hecate/models/conversation.py`：Conversation ORM 模型 + Pydantic schema（agent_id, title，CreateSchema/ReadSchema）
- [x] 2.9 创建 `src/hecate/models/document.py`：Document ORM 模型 + Pydantic schema（knowledge_base_id, filename, file_path, file_size, content_type, parsing_status, parsing_error, chunk_count）
- [x] 2.10 创建 `src/hecate/models/checkpoint.py`：Checkpoint ORM 模型 + Pydantic schema（session_id, superstep, node_id, channel_state JSONB, pending_writes JSONB, metadata JSONB，不可变约束）
- [x] 2.11 创建 Alembic 初始迁移脚本（9 张核心表：agents, conversations, sessions, messages, tools, knowledge_bases, documents, skills, checkpoints + 全部索引）
- [x] 2.12 编写数据模型单元测试（CRUD 操作、软删除、JSONB 字段、Conversation 自动创建回填、Document parsing_status 状态流转、Checkpoint 不可变校验）

## 3. Graph DSL + 编译器

- [x] 3.1 创建 `src/hecate/engine/types.py`：核心类型定义（NodeType, Command, Edge, NodeConfig, GraphConfig）
- [x] 3.2 创建 Graph DSL JSON Schema 定义文件（`schemas/graph-dsl.schema.json`）
- [x] 3.3 实现 `src/hecate/engine/graph_dsl.py`：JSON 解析 + 验证（jsonschema 校验）
- [x] 3.4 实现 `src/hecate/engine/compiler.py`：Graph 编译器（JSON → CompiledGraph：拓扑排序、Channel 写入权限映射、子图引用解析）
- [x] 3.5 实现三层 Agent 预设模板生成器（Guard→Plan→Sub-Agent → Graph JSON）
- [x] 3.6 编译器错误处理和友好错误消息
- [x] 3.7 编写 Graph DSL + 编译器单元测试（合法/非法 JSON、拓扑排序、权限映射、子图解析）

## 4. 执行引擎

- [x] 4.1 实现 `src/hecate/engine/channel.py`：Channel 状态管理（write/read/snapshot，可写/只读/注入权限控制）
- [x] 4.2 实现 `src/hecate/engine/checkpoint.py`：Checkpoint 持久化接口 + PostgreSQL 实现（save/load/list，不可变）
- [x] 4.3 实现 `src/hecate/engine/worker.py`：Worker 接口 + P1 线程池实现（concurrent.futures），WorkerResult 类型
- [x] 4.4 实现 `src/hecate/engine/pregel.py`：Pregel 运行时（superstep 循环：读 Channel → 分发 Worker → 收集结果 → 写 Channel → Checkpoint → 检查终止）
- [x] 4.5 实现 interrupt/恢复机制（WorkerResult.interrupt → 暂停 superstep → 保存 Checkpoint → 用户输入 → 恢复执行）
- [x] 4.6 实现子图执行（agent 类型节点：创建子 Channel → 递归 Pregel → 结果映射回父 Channel）
- [x] 4.7 实现 `src/hecate/engine/ports.py`：EnginePort 接口（Protocol/ABC，定义 7 个方法：llm_invoke, tool_execute, knowledge_query, checkpoint_save, checkpoint_load, conversation_load, conversation_save），引擎内部通过此接口调用能力服务层，不直接依赖具体服务实现
- [x] 4.8 编写执行引擎集成测试（线性图、条件分支、interrupt/恢复、子图嵌套）

## 5. API 层

- [x] 5.1 创建 `src/hecate/main.py`：FastAPI 应用初始化（CORS、异常处理、lifespan 事件）
- [x] 5.2 创建 `src/hecate/core/deps.py`：通用依赖注入（DB session、API Key 认证、当前 Agent）
- [x] 5.3 实现 `/api/agents` CRUD（创建/读取/更新/删除 Agent，含 Graph 配置）
- [x] 5.4 实现 `/api/sessions`（创建/列表/恢复/删除 Session）
- [x] 5.5 实现 `/api/tools`（列表内置工具 + MCP 发现的工具）
- [x] 5.6 实现 `/api/skills`（列表 + 按需加载 SKILL.md）
- [x] 5.7 实现 `/api/knowledge-bases`（创建/上传文档/检索）
- [x] 5.8 实现 `/v1/chat/completions`（OpenAI 兼容，接收 messages → 路由到 Agent → 流式/非流式响应）
- [x] 5.9 实现 `/v1/models`（返回已配置的 LLM 模型列表）
- [x] 5.10 实现 SSE streaming 响应格式（`data: {"choices": [{"delta": {"content": "..."}}]}`）
- [x] 5.11 实现 Rate Limiting（slowapi 或自建，每 API Key 每分钟请求数限制）
- [x] 5.12 编写 API 集成测试（使用 httpx AsyncClient，覆盖所有端点）
- [x] 5.13 实现 `/api/conversations` 端点：`GET /api/conversations` 列出对话（支持 agent_id 过滤和分页）、`GET /api/conversations/{id}` 获取对话详情（含关联消息列表）

## 6. LLM 模型路由

- [x] 6.1 实现 `src/hecate/services/llm/service.py`：LiteLLM 封装（acompletion + acompletion with streaming）
- [x] 6.2 实现 streaming 响应生成器（LiteLLM async generator → SSE chunk 格式转换）
- [x] 6.3 实现 tool calling 协议（function definitions → LLM tool_call → 执行 → tool role message 回注）
- [x] 6.4 实现模型降级策略（主模型失败 → 备选模型 → 错误响应）
- [x] 6.5 实现 `/v1/models` 模型列表（从 LiteLLM 配置读取可用模型）
- [x] 6.6 编写 LLM 服务测试（mock LiteLLM，验证 streaming、tool calling、降级）

## 7. RAG 管线

- [x] 7.1 实现 `src/hecate/services/rag/embedding.py`：BGE-M3 封装（encode dense + sparse，FP16 优化）
- [x] 7.2 实现 `src/hecate/services/rag/parser.py`：Docling 文档解析（支持 PDF/DOCX/HTML/Markdown 等 20+ 格式 → 纯文本）
- [x] 7.3 实现 `src/hecate/services/rag/chunker.py`：文本分片（512-1024 tokens，overlap 100-200，保留元数据）
- [x] 7.4 实现 `src/hecate/services/rag/indexer.py`：Qdrant 索引管理（创建 collection + dense/sparse 双向量配置 + upsert）
- [x] 7.5 实现 `src/hecate/services/rag/searcher.py`：Hybrid Search（dense + sparse fusion → Top-K）
- [x] 7.6 实现 Knowledge Base 服务（上传文档 → 解析 → 分片 → encode → 索引；查询 → hybrid search）
- [x] 7.7 实现 `src/hecate/services/rag/storage.py`：MinIO 集成（文档上传先存 MinIO → 返回 file_path → 解析管线从 MinIO 拉取原始文件，使用 minio-py async client）
- [x] 7.8 实现 documents 表状态追踪（上传后 parsing_status="pending" → 开始解析 "parsing" → 完成 "completed" 设置 chunk_count → 失败 "failed" 记录 parsing_error）
- [x] 7.9 编写 RAG 管线测试（mock embedding 和 Qdrant，验证分片、索引、检索流程、MinIO 上传/拉取、parsing_status 状态流转）

## 8. 安全层

- [x] 8.1 实现 `src/hecate/services/security/llm_guard.py`：LLM Guard Scanner 封装（scan_prompt + scan_output，四 Scanner 组合）
- [x] 8.2 实现 Anonymize/Deanonymize Vault 管理（PII 脱敏 → LLM → 还原）
- [x] 8.3 实现 NeMo Guardrails 基础配置（话题控制 colang 规则文件）
- [x] 8.4 实现安全中间件（在 LLM 调用前后执行 LLM Guard scan，在 Agent 入口执行 NeMo Guardrails）
- [x] 8.5 实现 API Key 认证中间件（Bearer token 验证，从数据库查询有效 key）
- [x] 8.6 编写安全层测试（验证 PII 脱敏/还原、注入检测、毒性检测）

## 9. MCP 集成

- [x] 9.1 实现 `src/hecate/services/mcp/client.py`：MCP Client 连接管理（连接 MCP Server → tools/list 发现）
- [x] 9.2 实现 MCP Tool 同步（发现 → 转换为 Hecate Tool 格式 → 写入 tools 表）
- [x] 9.3 实现 MCP Tool 调用（参数验证 → tools/call → 结果返回）
- [x] 9.4 编写 MCP 集成测试（mock MCP Server，验证发现和调用）

## 10. 端到端集成

- [x] 10.1 实现三层 Agent 模板到 Graph 的完整编译（Agent 配置 → Graph DSL → CompiledGraph）
- [x] 10.2 实现完整对话闭环（用户消息 → Guard 检查 → Planner 规划 → Tool/RAG 调用 → 响应）
- [x] 10.3 实现 tool calling 完整流程（LLM 返回 tool_call → 查找 Tool → 执行 → 结果回注 → LLM 继续）
- [x] 10.4 实现 RAG 检索集成（Agent 配置 knowledge_base → Planner 触发 RAG → 结果注入 context）
- [x] 10.5 编写端到端集成测试（创建 Agent → 创建 Session → 发送消息 → 收到响应，mock LLM）
- [x] 10.6 编写端到端测试：Agent 配置工具 + tool calling 完整闭环
- [x] 10.7 编写端到端测试：Agent 配置知识库 + RAG 检索完整闭环
- [x] 10.8 编写端到端测试：Session interrupt + resume 完整闭环
- [x] 10.9 编写端到端测试：多模型 fallback 降级
- [x] 10.10 编写 Docker Compose 冒烟测试

## 11. 文档和收尾

- [x] 11.1 编写 README.md（项目简介、快速开始、架构概览、API 文档链接）
- [x] 11.2 更新 `AGENTS.md` 反映代码仓库结构
- [x] 11.3 配置 CI（GitHub Actions：lint + type check + test）
- [x] 11.4 配置 pre-commit hooks（ruff format + mypy + pytest）
- [x] 11.5 自定义 FastAPI 自动生成的 OpenAPI spec（添加中文描述、请求/响应示例、错误模型文档、按标签分组端点）
