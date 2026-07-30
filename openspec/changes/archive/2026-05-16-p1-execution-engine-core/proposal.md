## Why — 为什么

Hecate 的顶层架构设计（10 项 ADR）已完成，但 P1 范围（19 个核心功能，月 1-3）缺乏可直接编码的详细规格。当前 `docs/design/architecture.md` 停留在模块级别粒度，需要精确到 API 签名、数据库表结构、消息格式和组件交互协议，才能开始编码。

Hecate's top-level architecture design (10 ADRs) is complete, but the P1 scope (19 core features, months 1-3) lacks detailed specifications ready for coding. The current `docs/design/architecture.md` stays at module-level granularity — it needs to be precise down to API signatures, database table structures, message formats, and component interaction protocols before coding can begin.

P1 的核心是执行引擎——它是所有其他模块的基石。引擎不就绪，API 层、RAG、安全、前端都无法真正落地。

The core of P1 is the execution engine — it is the foundation for all other modules. Without the engine ready, the API layer, RAG, security, and frontend cannot truly be implemented.

## What Changes — 变更内容

- 新建 Python 项目骨架（`pyproject.toml` + 目录结构 + 核心模块空包）
- 实现自建执行引擎核心：Graph DSL JSON Schema → 编译器 → Pregel 运行时 → Channel 状态管理 → Checkpoint 持久化 → Worker Pool 线程池
- 定义核心数据模型（Agent / Conversation / Session / Message / Tool / KnowledgeBase / Document / Skill / Checkpoint）的 PostgreSQL DDL（9 张表）和 Python ORM schema
- 实现 OpenAI 兼容 API（`/v1/chat/completions`）+ Hecate 管理 API（`/api/agents`、`/api/sessions` 等）
- 集成 LiteLLM 模型路由 + streaming + tool calling 协议
- 集成 BGE-M3 Embedding + Qdrant 混合索引的基础 RAG 管线（Docling 解析 → 分片 → encode → 检索）
- 集成 LLM Guard 4 Scanner（PromptInjection + Anonymize + Secrets + Toxicity）+ NeMo Guardrails 基础话题控制
- 集成 MCP Tool 发现和调用协议
- Docker Compose 部署配置（PostgreSQL + Qdrant + MinIO + Hecate 服务）

- Create Python project skeleton (`pyproject.toml` + directory structure + core module empty packages)
- Implement self-built execution engine core: Graph DSL JSON Schema → Compiler → Pregel Runtime → Channel State Management → Checkpoint Persistence → Worker Pool Thread Pool
- Define PostgreSQL DDL (9 tables) and Python ORM schemas for core data models (Agent / Conversation / Session / Message / Tool / KnowledgeBase / Document / Skill / Checkpoint)
- Implement OpenAI-compatible API (`/v1/chat/completions`) + Hecate Management API (`/api/agents`, `/api/sessions`, etc.)
- Integrate LiteLLM model routing + streaming + tool calling protocol
- Integrate basic RAG pipeline with BGE-M3 Embedding + Qdrant hybrid index (Docling parsing → chunking → encode → retrieval)
- Integrate LLM Guard 4 Scanners (PromptInjection + Anonymize + Secrets + Toxicity) + NeMo Guardrails basic topic control
- Integrate MCP Tool discovery and invocation protocol
- Docker Compose deployment configuration (PostgreSQL + Qdrant + MinIO + Hecate service)

## Capabilities — 能力

### New Capabilities — 新能力

- `graph-dsl`: Graph DSL JSON Schema 定义、编译器（JSON → CompiledGraph）、验证规则。支持 conversation、tool-call、condition、agent 四种节点类型，Command(goto/return/interrupt) 边协议
  — Graph DSL JSON Schema definition, compiler (JSON → CompiledGraph), validation rules. Supports four node types: conversation, tool-call, condition, agent; Command(goto/return/interrupt) edge protocol
- `execution-engine`: Pregel 运行时（superstep 调度）+ Channel 状态管理（可写/只读/注入口）+ Checkpoint 持久化（PostgreSQL）+ 内存缓存 + interrupt/恢复 + 子图支持
  — Pregel runtime (superstep scheduling) + Channel state management (writable/read-only/injection) + Checkpoint persistence (PostgreSQL) + memory cache + interrupt/resume + subgraph support
- `worker-pool`: Worker 接口定义 + P1 进程内线程池实现。Worker 接收只读 Channel 快照，通过 WorkerResult 返回结果和 interrupt 信号
  — Worker interface definition + P1 in-process thread pool implementation. Worker receives read-only Channel snapshots, returns results and interrupt signals via WorkerResult
- `data-model`: PostgreSQL DDL（9 张表）+ Python Pydantic schema + UUID + JSONB + 软删除
  — PostgreSQL DDL (9 tables) + Python Pydantic schema + UUID + JSONB + soft delete
- `api-gateway`: FastAPI 应用 + OpenAI 兼容层 + Hecate 管理 API + API Key 认证 + Rate Limiting
- `model-routing`: LiteLLM wrapper + streaming + tool calling protocol + model fallback strategy
- `rag-pipeline`: Docling parsing → text chunking → BGE-M3 encode → Qdrant hybrid index → Hybrid Search
- `security-layer`: LLM Guard 4 Scanners + NeMo Guardrails + API Key auth + Rate Limiting. Covers OWASP LLM01/02/05/07/10
- `mcp-integration`: MCP Tool discovery protocol + Tool invocation protocol. P1 client only
- `project-skeleton`: Python project structure + pyproject.toml + dependency management + Docker Compose

### Modified Capabilities — 修改的能力

（无已有 specs，全部为新建）— (No existing specs, all newly created)

## Impact — 影响

- **New codebase**: From scratch, Python backend + future TypeScript frontend
- **Core dependencies**: Python 3.12+, FastAPI, Pydantic v2, SQLAlchemy, LiteLLM, FlagEmbedding, qdrant-client, llm-guard, nemoguardrails, docling
- **Infrastructure**: PostgreSQL 16+, Qdrant 1.x, MinIO, Docker Compose
- **API contract**: OpenAI-compatible API without any extension fields; Hecate Management API follows RESTful + JSON + unified error format
- **Code estimate**: Engine core ~5900 lines, API layer ~2000 lines, RAG pipeline ~1500 lines, security layer ~800 lines, total P1 ~12000 lines

## P1 功能覆盖映射 — P1 Feature Coverage Mapping

10 个 Capability 与 feature-catalog.md P1 的 19 项功能对应关系 — Mapping of 10 capabilities to 19 P1 features:

| Capability | 覆盖的 P1 功能 — Covered P1 Features |
|-----------|--------------|
| `graph-dsl` | 1.3.1 ReAct Agent 循环、1.3.1a Plan-Execute 任务分解 |
| `execution-engine` | 1.3.1 ReAct Agent 循环、1.3.2 工具调用、1.3.3 流式输出、1.3.4 人工介入、1.3.5 错误恢复、5.1 内置工具、5.2 自定义工具、5.9 Skill 加载与管理 |
| `worker-pool` | （执行引擎基础设施）— Engine infrastructure |
| `data-model` | （所有功能的数据基础）— Data foundation for all features |
| `api-gateway` | 11.1 API 接口、8.4 对话日志 |
| `model-routing` | 6.1 多模型接入、6.3 模型降级 |
| `rag-pipeline` | 3.1.1 文档解析、3.2.1 向量检索、3.2.6 分块策略、3.3.1 知识库 CRUD |
| `security-layer` | AD-8 P1 安全基线 — Security baseline |
| `mcp-integration` | 5.3 MCP 客户端 |
| `project-skeleton` | 13.2 私有化部署（Docker Compose） |

## 数据模型对齐决策 — Data Model Alignment Decisions

评审发现 `architecture.md` 概念模型、`design.md` DDL、`specs/data-model` 三处数据模型存在不一致。决策如下 — Review found inconsistencies across three locations. Decisions:

**权威来源 — Source of Truth**: 以 `specs/data-model` 为准 — `specs/data-model` is authoritative.

| # | 不一致项 — Inconsistency | architecture.md | design.md (修正前 — Before) | specs/data-model (决策 — Decision) | 决策理由 — Rationale |
|---|---------|----------------|-------------------|------------------------|---------|
| 1 | Agent 系统提示词字段 — system prompt field | `persona: str` | `system_prompt TEXT` | `persona TEXT` | `persona` 语义更广 — broader semantics |
| 2 | Agent 工具关联 — tool association | `tools: List[ToolRef]` | `tool_ids UUID[]` | `tools JSONB` | JSONB 更灵活 — more flexible |
| 3 | Agent 知识库关联 — KB association | `knowledge_bases: List[UUID]` | `knowledge_base_id UUID` | `knowledge_base_ids JSONB` | 一个 Agent 可关联多个知识库 — one agent can have multiple KBs |
| 4 | Tool 类型字段名 — type field name | `source: ToolSource` | `type VARCHAR(20)` | `source VARCHAR(20)` | `source` 与 `ToolSource` 枚举一致 — consistent with ToolSource enum |
| 5 | Tool Schema 字段 — schema fields | `parameters: dict` | `config JSONB` | `parameters JSONB` + `returns JSONB` | 明确区分输入/输出 Schema — distinguish input/output schemas |
| 6 | messages 外键 — foreign key | `conversation_id` | `session_id` | `conversation_id` | 消息按会话聚合 — messages aggregate by conversation |
| 7 | sessions 表字段 — table fields | 含 conversation_id, current_node, checkpoint_id | 仅有 agent_id, status, metadata | 含 conversation_id, current_node, checkpoint_id, metadata | 完整记录执行上下文 — complete execution context |
| 8 | agents 表缺少 workspace_id | `workspace_id: UUID` | 无 | `workspace_id UUID NOT NULL` | 多租户预留 — multi-tenant reservation |
| 9 | knowledge_bases 表 | 含 chunk_strategy, chunk_overlap | 无 | 含 chunk_strategy, chunk_overlap | 分块策略是 RAG 核心配置 — chunking strategy is core RAG config |
| 10 | skills 表 | 含 instructions, allowed_tools, metadata | 仅有 path, enabled | 含 instructions, allowed_tools, metadata, scripts, references, max_tokens, auto_load | 完整支持 AD-4 的 SKILL.md 格式 — full SKILL.md support |
