## Why

Hecate 的顶层架构设计（10 项 ADR）已完成，但 P1 范围（19 个核心功能，月 1-3）缺乏可直接编码的详细规格。当前 `docs/design/architecture.md` 停留在模块级别粒度，需要精确到 API 签名、数据库表结构、消息格式和组件交互协议，才能开始编码。

P1 的核心是执行引擎——它是所有其他模块的基石。引擎不就绪，API 层、RAG、安全、前端都无法真正落地。

## What Changes

- 新建 Python 项目骨架（`pyproject.toml` + 目录结构 + 核心模块空包）
- 实现自建执行引擎核心：Graph DSL JSON Schema → 编译器 → Pregel 运行时 → Channel 状态管理 → Checkpoint 持久化 → Worker Pool 线程池
- 定义核心数据模型（Agent / Conversation / Session / Message / Tool / KnowledgeBase / Document / Skill / Checkpoint）的 PostgreSQL DDL（9 张表）和 Python ORM schema
- 实现 OpenAI 兼容 API（`/v1/chat/completions`）+ Hecate 管理 API（`/api/agents`、`/api/sessions` 等）
- 集成 LiteLLM 模型路由 + streaming + tool calling 协议
- 集成 BGE-M3 Embedding + Qdrant 混合索引的基础 RAG 管线（Docling 解析 → 分片 → encode → 检索）
- 集成 LLM Guard 4 Scanner（PromptInjection + Anonymize + Secrets + Toxicity）+ NeMo Guardrails 基础话题控制
- 集成 MCP Tool 发现和调用协议
- Docker Compose 部署配置（PostgreSQL + Qdrant + MinIO + Hecate 服务）

## Capabilities

### New Capabilities

- `graph-dsl`: Graph DSL JSON Schema 定义、编译器（JSON → CompiledGraph）、验证规则。支持 conversation、tool-call、condition、agent 四种节点类型，Command(goto/return/interrupt) 边协议
- `execution-engine`: Pregel 运行时（superstep 调度）+ Channel 状态管理（可写/只读/注入口）+ Checkpoint 持久化（PostgreSQL）+ 内存缓存 + interrupt/恢复 + 子图支持
- `worker-pool`: Worker 接口定义 + P1 进程内线程池实现。Worker 接收只读 Channel 快照，通过 WorkerResult 返回结果和 interrupt 信号
- `data-model`: PostgreSQL DDL（9 张表）+ Python Pydantic schema + UUID + JSONB + 软删除。覆盖 Agent、Conversation、Session、Message、Tool、KnowledgeBase、Document、Skill、Checkpoint 实体。Conversation 与 Session 为 1:1 关系（session.conversation_id 为 NULL 时自动创建 Conversation）。Document 追踪 RAG 文档解析状态（pending → parsing → completed/failed），Checkpoint 持久化执行引擎每步状态
- `api-gateway`: FastAPI 应用 + OpenAI 兼容层（`/v1/chat/completions`、`/v1/models`）+ Hecate 管理 API（`/api/agents`、`/api/sessions`、`/api/tools`、`/api/skills`、`/api/knowledge-bases`）+ API Key 认证 + Rate Limiting
- `model-routing`: LiteLLM 封装层 + streaming 响应 + tool calling 协议（函数定义 → LLM 返回 tool_call → 执行 → 结果回注）+ 模型降级策略
- `rag-pipeline`: Docling 文档解析 → 文本分片（512-1024 tokens）→ BGE-M3 encode（dense+sparse）→ Qdrant 混合索引 → Hybrid Search → Top-K 结果。P1 仅支持上传和检索，不支持自动同步
- `security-layer`: LLM Guard 4 Scanner 集成（PromptInjection + Anonymize + Secrets + Toxicity）+ NeMo Guardrails 基础话题控制 + API Key 认证 + Rate Limiting。覆盖 OWASP LLM01/02/05/07/10
- `mcp-integration`: MCP Tool 发现协议（MCP Server 连接 → Tool 列表同步）+ Tool 调用协议（参数验证 → 执行 → 结果返回）。P1 仅做 client
- `project-skeleton`: Python 项目结构 + pyproject.toml + 依赖管理 + Docker Compose（PostgreSQL + Qdrant + MinIO + Hecate）+ 开发环境配置

### Modified Capabilities

（无已有 specs，全部为新建）

## Impact

- **新建代码库**: 从零开始，Python 后端 + 未来 TypeScript 前端
- **核心依赖**: Python 3.12+, FastAPI, Pydantic v2, SQLAlchemy, LiteLLM, FlagEmbedding, qdrant-client, llm-guard, nemoguardrails, docling, qdrant-client
- **基础设施**: PostgreSQL 16+, Qdrant 1.x, MinIO, Docker Compose
- **API 契约**: OpenAI 兼容 API 不做任何扩展字段；Hecate 管理 API 遵循 RESTful + JSON + 统一错误格式
- **代码量估算**: 执行引擎核心 ~5900 行，API 层 ~2000 行，RAG 管线 ~1500 行，安全层 ~800 行，总计 P1 约 ~12000 行
- **数据模型对齐说明**: `specs/data-model` 为数据模型定义的权威来源（Source of Truth）。`architecture.md` 概念模型和 `design.md` DDL 均需与 `specs/data-model` 保持一致。三处不一致的具体字段和修正决策详见末节"数据模型对齐决策"

## P1 功能覆盖映射

10 个 Capability 与 feature-catalog.md P1 的 19 项功能对应关系：

| Capability | 覆盖的 P1 功能 |
|-----------|--------------|
| `graph-dsl` | 1.3.1 ReAct Agent 循环、1.3.1a Plan-Execute 任务分解 |
| `execution-engine` | 1.3.1 ReAct Agent 循环、1.3.2 工具调用、1.3.3 流式输出、1.3.4 人工介入、1.3.5 错误恢复、5.1 内置工具、5.2 自定义工具、5.9 Skill 加载与管理 |
| `worker-pool` | （执行引擎基础设施，支撑 1.3.2 并行工具调用和 1.3.5 错误恢复） |
| `data-model` | （所有功能的数据基础，支撑 8.4 对话日志、3.3.1 知识库 CRUD） |
| `api-gateway` | 11.1 API 接口（OpenAI 兼容 `/v1/` + Hecate 管理 `/api/`）、8.4 对话日志 |
| `model-routing` | 6.1 多模型接入、6.3 模型降级 |
| `rag-pipeline` | 3.1.1 文档解析、3.2.1 向量检索、3.2.6 分块策略、3.3.1 知识库 CRUD |
| `security-layer` | （AD-8 P1 安全基线：LLM Guard 四 Scanner + NeMo Guardrails + API Key 认证 + Rate Limiting，非独立功能项但为安全必需） |
| `mcp-integration` | 5.3 MCP 客户端 |
| `project-skeleton` | 13.2 私有化部署（Docker Compose） |

> 功能覆盖完整性：P1 的 19 项功能全部被上述 10 个 Capability 覆盖。`security-layer` 不直接对应 feature-catalog 中的 P1 功能项，但 AD-8 确认其为 P1 安全基线的必要组成。

## 数据模型对齐决策

评审发现 `architecture.md` 概念模型、`design.md` DDL、`specs/data-model` 三处数据模型存在不一致。决策如下：

**权威来源**: 以 `specs/data-model` 为准，`architecture.md` 和 `design.md` 需同步更新。

| # | 不一致项 | architecture.md | design.md (修正前) | specs/data-model (决策) | 决策理由 |
|---|---------|----------------|-------------------|------------------------|---------|
| 1 | Agent 系统提示词字段 | `persona: str` | `system_prompt TEXT` | `persona TEXT` | `persona` 语义更广，含人设和背景，优于纯技术术语 `system_prompt` |
| 2 | Agent 工具关联 | `tools: List[ToolRef]` | `tool_ids UUID[]` | `tools JSONB` | JSONB 更灵活，可存储工具引用+配置，优于纯 UUID 数组 |
| 3 | Agent 知识库关联 | `knowledge_bases: List[UUID]` | `knowledge_base_id UUID`（单数） | `knowledge_base_ids JSONB` | 一个 Agent 可关联多个知识库（feature 3.2.7），JSONB 支持列表 |
| 4 | Tool 类型字段名 | `source: ToolSource` | `type VARCHAR(20)` | `source VARCHAR(20)` | `source` 与 `ToolSource` 枚举一致，避免与 Node.type 混淆 |
| 5 | Tool Schema 字段 | `parameters: dict` | `config JSONB` | `parameters JSONB` + `returns JSONB` | 明确区分输入/输出 Schema，`config` 语义模糊 |
| 6 | messages 外键 | `conversation_id` | `session_id` | `conversation_id` | 消息按会话聚合，conversation 是消息的自然分组单位 |
| 7 | sessions 表字段 | 含 conversation_id, current_node, checkpoint_id | 仅有 agent_id, status, metadata | 含 conversation_id, current_node, checkpoint_id, metadata | 完整记录执行上下文，支持 interrupt 恢复 |
| 8 | agents 表缺少 workspace_id | `workspace_id: UUID` | 无 | `workspace_id UUID NOT NULL` | 多租户预留（P3 启用 RLS），P1 使用默认 workspace |
| 9 | knowledge_bases 表 | 含 chunk_strategy, chunk_overlap | 无 | 含 chunk_strategy, chunk_overlap | 分块策略是 RAG 核心配置（feature 3.2.6） |
| 10 | skills 表 | 含 instructions, allowed_tools, metadata | 仅有 path, enabled | 含 instructions, allowed_tools, metadata, scripts, references, max_tokens, auto_load | 完整支持 AD-4 的 SKILL.md 格式 + 多源发现 |

**补充缺失表**: `specs/data-model` 引用了 `conversation_id` 和 `workspace_id` 但未定义对应表。修正为：
- 新增 `conversations` 表（id, agent_id, title, created_at, updated_at, deleted_at）
- 新增 `checkpoints` 表（id, session_id, superstep, node_id, channel_state JSONB, pending_writes JSONB, metadata JSONB, created_at）
- 新增 `documents` 表（id, knowledge_base_id, filename, file_path, file_size, content_type, parsing_status, parsing_error, chunk_count, created_at, updated_at, deleted_at）— 追踪 RAG 文档从上传到解析完成的全生命周期
