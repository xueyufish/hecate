## Context

Hecate 是一个开源、自托管、模型无关、MCP-first 的企业级 Agent 平台。当前仓库仅包含设计文档（调研笔记 28 份、综合报告 7 份、架构设计 v0.2、功能全集 156 项、10 项 ADR），无任何源代码。

P1 范围（月 1-3，19 个核心功能）目标是"能跑通一个完整 Agent 应用"：创建 Agent → 配置模型/工具/知识库 → 对话测试 → 私有化部署。

技术决策已通过 AD-1 到 AD-10 确认：
- AD-1: Graph 编排为主，三层 Agent 为预设模板
- AD-2: 五层架构（接入→编排→执行引擎→能力服务→基础设施）
- AD-3: Checkpoint 持久化 + 内存缓存
- AD-4: P1 SKILL.md + 多源发现
- AD-5: Worker Pool 渐进式（P1 线程池）
- AD-6: 四级记忆渐进式（P1 L2 简化 + L4 RAG，BGE-M3 Embedding）
- AD-7: 所有模式统一为 Graph 模板（P1 仅层级委派）
- AD-8: LLM Guard 内层 + NeMo Guardrails 外层，OWASP 风险映射
- AD-9: OpenAI 兼容 `/v1/` + Hecate 管理 `/api/` 双轨
- AD-10: React Flow + JSON DSL（P2 交付）

## Goals / Non-Goals

**Goals:**

- 实现自建执行引擎核心（~5900 行），借鉴 LangGraph 设计模式（Channel/Checkpoint/Pregel/interrupt/子图），不依赖 LangGraph 代码
- 实现完整的 Agent 对话闭环：用户通过 `/v1/chat/completions` 发送消息 → Agent 调用 LLM → tool calling → RAG 检索 → 返回响应
- 实现三层 Agent 预设模板（Guard→Plan→Sub-Agent），作为 Graph 模板的第一个实例
- 实现 Docker Compose 一键部署（PostgreSQL + Qdrant + MinIO + Hecate）
- 所有 API 可通过 curl/Postman 测试，无需前端

**Non-Goals:**

- 前端画布 UI（P2）
- 多 Agent 编排（移交/流水线/广播等，P2-P3）
- 工作流可视化编辑器（P2）
- RBAC / 多租户 / SSO（P3）
- 记忆 L1（MemoryBlock）和 L3（用户记忆）（P2）
- Model marketplace / Plugin marketplace（P4）
- NL2Agent / NL2Workflow（P4）
- Temporal 分布式执行（P3）
- Kubernetes 部署（P3）

## Decisions

### D1: 项目结构 — monorepo src layout

```
hecate/
├── src/
│   └── hecate/
│       ├── __init__.py
│       ├── main.py                    # FastAPI app entry
│       ├── api/                       # API 层
│       │   ├── v1/                    # OpenAI 兼容
│       │   │   ├── chat.py
│       │   │   └── models.py
│       │   └── management/            # Hecate 管理 API
│       │       ├── agents.py
│       │       ├── sessions.py
│       │       ├── tools.py
│       │       ├── skills.py
│       │       └── knowledge.py
│       ├── engine/                    # 执行引擎
│       │   ├── graph_dsl.py           # Graph DSL JSON Schema 定义
│       │   ├── compiler.py            # Graph 编译器
│       │   ├── channel.py             # Channel 状态管理
│       │   ├── checkpoint.py          # Checkpoint 持久化
│       │   ├── pregel.py              # Pregel 运行时
│       │   ├── worker.py              # Worker Pool
│       │   ├── command.py             # Command / interrupt
│       │   ├── ports.py               # EnginePort 接口（引擎与能力服务层的解耦边界）
│       │   └── types.py               # 核心类型定义
│       ├── models/                    # 数据模型
│       │   ├── base.py                # Base model + mixins
│       │   ├── agent.py
│       │   ├── conversation.py
│       │   ├── session.py
│       │   ├── message.py
│       │   ├── tool.py
│       │   ├── knowledge.py
│       │   ├── document.py
│       │   ├── skill.py
│       │   └── checkpoint.py
│       ├── services/                  # 能力服务
│       │   ├── llm/                   # LiteLLM 封装
│       │   ├── rag/                   # RAG 管线
│       │   ├── security/              # 安全层
│       │   └── mcp/                   # MCP 集成
│       └── core/                      # 核心配置
│           ├── config.py
│           ├── database.py
│           └── deps.py                # FastAPI 依赖注入
├── tests/
├── docker/
│   └── docker-compose.yml
├── pyproject.toml
└── docs/                              # 已有设计文档
```

**理由**: src layout 避免 import 混淆，monorepo 降低协调成本，P1 不需要微服务拆分。

### D2: Web 框架 — FastAPI + Pydantic v2

- FastAPI：原生 async、自动 OpenAPI 文档、依赖注入、SSE streaming 支持
- Pydantic v2：数据验证、JSON Schema 生成、ORM 模式（`from_attributes=True`）
- SQLAlchemy 2.0 async：异步 ORM + PostgreSQL asyncpg driver

**替代方案**: Flask（无原生 async）、Django（过重）、Starlette（FastAPI 底层，缺少自动文档）

### D3: Graph DSL — JSON Schema + Python 编译器

Graph 定义为 JSON 文档，编译器将其转为 `CompiledGraph`（可执行的 Python 对象）：

```python
# Graph DSL JSON 结构
{
  "nodes": [
    {"id": "guard", "type": "agent", "ref": "guard-agent"},
    {"id": "planner", "type": "agent", "ref": "planner-agent"},
    {"id": "tool_call", "type": "tool-call"},
    {"id": "condition", "type": "condition", "expr": "has_tool_call"}
  ],
  "edges": [
    {"from": "guard", "to": "planner", "trigger": "continue"},
    {"from": "planner", "to": "tool_call", "trigger": "Command(goto='tool_call')"},
    {"from": "planner", "to": "__end__", "trigger": "Command(return='response')"}
  ],
  "entry": "guard",
  "state": {
    "messages": {"type": "list", "default": []},
    "context": {"type": "dict", "default": {}}
  }
}
```

编译器产出：
- 拓扑排序的执行计划
- Channel 写入权限映射（哪个节点能写哪个 Channel）
- 子图引用解析

### D4: 执行引擎 — Pregel + Channel + Checkpoint

```
Pregel 运行时:
  ┌─────────────────────────────┐
  │  Superstep Loop              │
  │  1. 读取 Channel 快照        │
  │  2. 分发到 Worker Pool       │
  │  3. 收集 WorkerResult        │
  │  4. 写入 Channel             │
  │  5. 持久化 Checkpoint        │
  │  6. 检查终止条件             │
  └─────────────────────────────┘
```

关键接口：
- `Channel`: `write(key, value)`, `read(key)`, `snapshot() -> dict`
- `Checkpoint`: `save(thread_id, checkpoint_id, channel_snapshot)`, `load(thread_id, checkpoint_id) -> dict`
- `Worker`: `execute(node_config, channel_snapshot) -> WorkerResult`
- `WorkerResult`: `channel_updates: dict`, `interrupt: Optional[interrupt_data]`, `error: Optional[Exception]`

### D5: 数据库设计 — PostgreSQL + UUID + JSONB

```sql
-- 9 张核心表（以 specs/data-model 为权威来源）

CREATE TABLE agents (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id UUID NOT NULL DEFAULT '00000000-0000-0000-0000-000000000000',
    name VARCHAR(255) NOT NULL,
    persona TEXT,
    model_config JSONB NOT NULL DEFAULT '{}',
    mode VARCHAR(50) NOT NULL DEFAULT 'chat',
    workflow_id UUID,
    tools JSONB DEFAULT '[]',
    skills JSONB DEFAULT '[]',
    knowledge_base_ids JSONB DEFAULT '[]',
    risk_level VARCHAR(20) DEFAULT 'LOW',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    deleted_at TIMESTAMPTZ
);

CREATE TABLE conversations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_id UUID NOT NULL REFERENCES agents(id),
    title VARCHAR(255),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    deleted_at TIMESTAMPTZ
);

CREATE TABLE sessions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    conversation_id UUID REFERENCES conversations(id),
    agent_id UUID NOT NULL REFERENCES agents(id),
    status VARCHAR(20) NOT NULL DEFAULT 'active',
    current_node VARCHAR(100),
    checkpoint_id UUID,
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE messages (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    conversation_id UUID NOT NULL REFERENCES conversations(id),
    role VARCHAR(20) NOT NULL,
    content TEXT NOT NULL,
    tool_calls JSONB,
    tool_call_id VARCHAR(100),
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE tools (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id UUID NOT NULL DEFAULT '00000000-0000-0000-0000-000000000000',
    name VARCHAR(255) NOT NULL,
    description TEXT NOT NULL,
    source VARCHAR(20) NOT NULL,
    parameters JSONB NOT NULL,
    returns JSONB,
    risk_level VARCHAR(20) DEFAULT 'LOW',
    approval_required BOOLEAN DEFAULT FALSE,
    mcp_server VARCHAR(255),
    mcp_tool_name VARCHAR(255),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    deleted_at TIMESTAMPTZ
);

CREATE TABLE knowledge_bases (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id UUID NOT NULL DEFAULT '00000000-0000-0000-0000-000000000000',
    name VARCHAR(255) NOT NULL,
    description TEXT,
    embedding_model VARCHAR(100) NOT NULL DEFAULT 'BAAI/bge-m3',
    chunk_strategy VARCHAR(20) NOT NULL DEFAULT 'fixed',
    chunk_size INTEGER NOT NULL DEFAULT 512,
    chunk_overlap INTEGER NOT NULL DEFAULT 100,
    qdrant_collection VARCHAR(255) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    deleted_at TIMESTAMPTZ
);

CREATE TABLE documents (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    knowledge_base_id UUID NOT NULL REFERENCES knowledge_bases(id),
    filename VARCHAR(255) NOT NULL,
    file_path TEXT NOT NULL,
    file_size BIGINT DEFAULT 0,
    content_type VARCHAR(100),
    parsing_status VARCHAR(20) NOT NULL DEFAULT 'pending',
    parsing_error TEXT,
    chunk_count INTEGER DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    deleted_at TIMESTAMPTZ
);

CREATE TABLE skills (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(255) NOT NULL,
    description TEXT NOT NULL,
    source VARCHAR(20) NOT NULL,
    instructions TEXT NOT NULL,
    allowed_tools JSONB DEFAULT '[]',
    metadata JSONB DEFAULT '{}',
    scripts JSONB DEFAULT '[]',
    references JSONB DEFAULT '[]',
    max_tokens INTEGER DEFAULT 2000,
    auto_load BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    deleted_at TIMESTAMPTZ
);

CREATE TABLE checkpoints (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id UUID NOT NULL REFERENCES sessions(id),
    superstep INTEGER NOT NULL,
    node_id VARCHAR(100),
    channel_state JSONB NOT NULL DEFAULT '{}',
    pending_writes JSONB DEFAULT '[]',
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 索引
CREATE INDEX idx_agents_workspace ON agents(workspace_id) WHERE deleted_at IS NULL;
CREATE INDEX idx_conversations_agent ON conversations(agent_id) WHERE deleted_at IS NULL;
CREATE INDEX idx_sessions_agent ON sessions(agent_id);
CREATE INDEX idx_sessions_conversation ON sessions(conversation_id);
CREATE INDEX idx_messages_conversation ON messages(conversation_id, created_at);
CREATE UNIQUE INDEX idx_tools_workspace_name ON tools(workspace_id, name) WHERE deleted_at IS NULL;
CREATE INDEX idx_documents_kb ON documents(knowledge_base_id) WHERE deleted_at IS NULL;
CREATE UNIQUE INDEX idx_skills_name ON skills(name) WHERE deleted_at IS NULL;
CREATE INDEX idx_checkpoints_session ON checkpoints(session_id, superstep);
```

**理由**: UUID 避免 ID 可预测，JSONB 灵活存储配置，软删除支持审计追踪。Conversation 与 Session 为 1:1 关系（session.conversation_id 为 NULL 时自动创建）。Document 表追踪 RAG 文档从上传到解析完成的全生命周期，file_path 指向 MinIO 存储。Checkpoint 表按 session_id 索引支持快速恢复。

### D6: API 双轨设计

OpenAI 兼容层（`/v1/`）：
- 严格遵循 OpenAI Chat Completions API 规范
- 支持 streaming（SSE）、tool calling、function calling
- 不扩展任何字段名
- 通过 `extra_body` 支持非标功能（如 session_id）

Hecate 管理 API（`/api/`）：
- RESTful 风格，统一错误格式 `{"error": {"code": "string", "message": "string", "details": {}}}`
- 认证：`Authorization: Bearer <api_key>`
- 分页：`?page=1&page_size=20`
- 过滤：`?status=active&name=xxx`

### D7: LLM 集成 — LiteLLM 封装

```python
class LLMService:
    async def chat(self, messages, model, tools=None, stream=False) -> ChatResponse:
        # LiteLLM 统一调用，自动路由到正确 provider
        response = await litellm.acompletion(
            model=model, messages=messages, tools=tools, stream=stream
        )
        return response

    async def chat_stream(self, messages, model, tools=None) -> AsyncGenerator[str, None]:
        # SSE streaming — litellm.acompletion(stream=True) 返回 async generator，不应再 await
        async for chunk in litellm.acompletion(
            model=model, messages=messages, tools=tools, stream=True
        ):
            yield chunk
```

Tool calling 协议：
1. Agent 配置中声明可用 tools（JSON Schema function definitions）
2. LLM 返回 `tool_call`（name + arguments）
3. 执行引擎查找并调用 tool
4. 结果作为 `tool` role message 回注
5. LLM 继续生成

### D8: RAG 管线 — Docling + BGE-M3 + Qdrant

```
上传文档 → Docling 解析(20+ 格式) → 文本分片(512-1024 tokens, overlap 100-200)
    → BGE-M3 encode(dense 1024维 + sparse token weights)
    → Qdrant upsert(dense vector + sparse vector)

检索: Query → BGE-M3 encode → Qdrant hybrid search(dense + sparse fusion)
    → Top-K → 重排序(可选) → 注入 LLM context
```

P1 仅支持：上传文档、自动分片索引、hybrid search。
P2 增加：bge-reranker-v2-m3 精排、自动同步、解析状态追踪。

### D9: 安全层 — 双层防护

调用链路（与 AD-8 安全分层架构一致）：

```
用户请求
  │
  ▼
┌─────────────────────────────────────┐
│ API 层 — NeMo Guardrails (外层)      │  对话流程控制、话题约束、行为边界
│ 位置：FastAPI 中间件，在路由处理前/后执行 │
└──────────────┬──────────────────────┘
               │
               ▼
┌─────────────────────────────────────┐
│ 执行引擎 — LLM Guard (内层)          │  内容级安全扫描
│ 位置：LLM 节点执行前后 hook            │
│ Input Scanners:                      │
│   PromptInjection (DeBERTa-v3, 0.5)  │
│   Anonymize (Presidio + BERT NER)    │
│   Secrets (detect-secrets)           │
│   Toxicity                           │
│ Output Scanners:                     │
│   Sensitive (PII)                    │
│   Toxicity                           │
└──────────────┬──────────────────────┘
               │
               ▼
          LLM 推理
```

P1 四 Scanner 配置：
- `PromptInjection`：DeBERTa-v3 分类，阈值 0.5
- `Anonymize`：Presidio + BERT NER，PII 脱敏
- `Secrets`：detect-secrets，API Key/Token 检测
- `Toxicity`：毒性内容检测（输入+输出）

### D10: MCP 集成 — Client-only

P1 仅实现 MCP Client：
1. 连接配置：在 Tool 配置中声明 MCP Server URL
2. 启动时发现：连接 MCP Server → `tools/list` → 同步到 Hecate Tool 表
3. 调用时执行：`tools/call(name, arguments)` → 结果返回

不实现 MCP Server（P2）。

### D11: 部署 — Docker Compose

```yaml
services:
  hecate-api:
    build: .
    ports: ["8000:8000"]
    depends_on: [postgres, qdrant, minio]
    env_file: .env

  postgres:
    image: postgres:16
    volumes: [pgdata:/var/lib/postgresql/data]

  qdrant:
    image: qdrant/qdrant:latest
    volumes: [qdrant_data:/qdrant/storage]

  minio:
    image: minio/minio
    command: server /data --console-address ":9001"
    volumes: [minio_data:/data]
```

MinIO 用途：RAG 原始文档存储。用户上传的 PDF/Word/PPT 等文档先存入 MinIO（`documents.file_path` 指向 MinIO 路径），再由 Docling 解析管线拉取解析。分离原始文件与解析后的文本/向量，支持重新解析和格式转换。

## Risks / Trade-offs

| 风险 | 影响 | 缓解 |
|------|------|------|
| Graph DSL 编译器复杂度 | 编译错误难以调试 | 先支持最简 Graph（线性链 + 条件分支），迭代增加复杂拓扑 |
| LLM Guard 性能开销 | 每次调用增加 30-200ms（CPU） | ONNX Runtime 优化（P2）；P1 可配置开关 |
| BGE-M3 内存占用 | FP16 ~1.5 GB 显存，CPU ~1.2 GB | 开发环境可用 CPU；生产环境建议单 GPU |
| PyTorch 依赖体积 | PyTorch 安装包 ~2 GB | 生产镜像预构建；开发用 CPU-only torch |
| 中文 PII 检测精度 | LLM Guard 默认 NER 英文优化 | P2 引入 `gyr66/bert-base-chinese-finetuned-ner` |
| LangGraph 设计模式适配 | 概念映射可能不完全 | 严格接口边界，核心引擎零 `langchain_core` 导入 |
| 单仓库体积增长 | docs/ + refs/pdf/ 已有大量文件 | PDF 文件不在 Docker 构建上下文中 |
| Checkpoint 写入延迟 | 每步同步写 PostgreSQL | P1 可接受（非高频场景）；P2 异步写入 + 内存缓存 |
