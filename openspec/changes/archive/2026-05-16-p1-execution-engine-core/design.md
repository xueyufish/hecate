## Context — 背景

Hecate 是一个开源、自托管、模型无关、MCP-first 的企业级 Agent 平台。当前仓库仅包含设计文档（调研笔记 28 份、综合报告 7 份、架构设计 v0.2、功能全集 156 项、10 项 ADR），无任何源代码。

Hecate is an open-source, self-hosted, model-agnostic, MCP-first enterprise-grade Agent platform. The current repository contains only design documents (28 research notes, 7 comprehensive reports, architecture design v0.2, 156 feature items, 10 ADRs), with no source code.

P1 范围（月 1-3，19 个核心功能）目标是"能跑通一个完整 Agent 应用"：创建 Agent → 配置模型/工具/知识库 → 对话测试 → 私有化部署。

P1 scope (months 1-3, 19 core features) targets "a complete runnable Agent application": create Agent → configure model/tools/knowledge base → conversation testing → private deployment.

技术决策已通过 AD-1 到 AD-10 确认 — Technical decisions confirmed via AD-1 to AD-10:
- AD-1: Graph orchestration primary, three-tier Agent as preset template
- AD-2: Five-layer architecture (Access → Orchestration → Execution Engine → Capability Services → Infrastructure)
- AD-3: Checkpoint persistence + memory cache
- AD-4: P1 SKILL.md + multi-source discovery
- AD-5: Progressive Worker Pool (P1 thread pool)
- AD-6: Progressive four-level memory (P1 simplified L2 + L4 RAG, BGE-M3 Embedding)
- AD-7: All modes unified as Graph templates (P1 hierarchical delegation only)
- AD-8: LLM Guard inner layer + NeMo Guardrails outer layer, OWASP risk mapping
- AD-9: OpenAI-compatible `/v1/` + Hecate Management `/api/` dual track
- AD-10: React Flow + JSON DSL (P2 delivery)

## Goals / Non-Goals — 目标/非目标

**Goals — 目标:**
- 实现自建执行引擎核心（~5900 行），借鉴 LangGraph 设计模式（Channel/Checkpoint/Pregel/interrupt/子图），不依赖 LangGraph 代码
  — Implement self-built execution engine core (~5900 lines), drawing from LangGraph design patterns (Channel/Checkpoint/Pregel/interrupt/subgraph), without depending on LangGraph code
- 实现完整的 Agent 对话闭环：用户通过 `/v1/chat/completions` 发送消息 → Agent 调用 LLM → tool calling → RAG 检索 → 返回响应
  — Implement complete Agent conversation loop: user sends message via `/v1/chat/completions` → Agent calls LLM → tool calling → RAG retrieval → returns response
- 实现三层 Agent 预设模板（Guard→Plan→Sub-Agent），作为 Graph 模板的第一个实例
  — Implement three-tier Agent preset template (Guard→Plan→Sub-Agent) as first instance of Graph templates
- 实现 Docker Compose 一键部署（PostgreSQL + Qdrant + MinIO + Hecate）
- 所有 API 可通过 curl/Postman 测试，无需前端
  — All APIs testable via curl/Postman, no frontend needed

**Non-Goals — 非目标:**
- Frontend canvas UI (P2)
- Multi-Agent orchestration (handoff/pipeline/broadcast, P2-P3)
- Workflow visual editor (P2)
- RBAC / Multi-tenant / SSO (P3)
- Memory L1 (MemoryBlock) and L3 (User Memory) (P2)
- Model marketplace / Plugin marketplace (P4)
- NL2Agent / NL2Workflow (P4)
- Temporal distributed execution (P3)
- Kubernetes deployment (P3)

## Decisions — 决策

### D1: 项目结构 — monorepo src layout — Project structure

```
hecate/
├── src/
│   └── hecate/
│       ├── main.py                    # FastAPI app entry
│       ├── api/v1/                    # OpenAI-compatible
│       ├── api/management/            # Hecate management API
│       ├── engine/                    # Execution engine
│       ├── models/                    # Data models
│       ├── services/                  # Capability services
│       └── core/                      # Core configuration
├── tests/
├── docker/
├── pyproject.toml
└── docs/
```

**理由 — Rationale**: src layout 避免 import 混淆，monorepo 降低协调成本，P1 不需要微服务拆分。
— src layout avoids import confusion, monorepo reduces coordination cost, P1 doesn't need microservice split.

### D2: Web 框架 — FastAPI + Pydantic v2 — Web framework

- FastAPI: native async, auto OpenAPI docs, DI, SSE streaming support
- Pydantic v2: data validation, JSON Schema generation, ORM mode
- SQLAlchemy 2.0 async: async ORM + PostgreSQL asyncpg driver

### D3: Graph DSL — JSON Schema + Python 编译器 — JSON Schema + Python Compiler

Graph defined as JSON document, compiler transforms to executable `CompiledGraph` (Python object):

```python
{
  "nodes": [...],
  "edges": [...],
  "entry": "guard",
  "state": {...}
}
```

Compiler produces: topologically sorted execution plan, Channel write permission mapping, subgraph reference resolution.

### D4: 执行引擎 — Pregel + Channel + Checkpoint — Execution engine

```
Pregel Runtime Superstep Loop:
  1. Read Channel snapshot
  2. Dispatch to Worker Pool
  3. Collect WorkerResult
  4. Write to Channel
  5. Persist Checkpoint
  6. Check termination condition
```

Key interfaces:
- `Channel`: `write(key, value)`, `read(key)`, `snapshot() -> dict`
- `Checkpoint`: `save(thread_id, checkpoint_id, channel_snapshot)`, `load(thread_id, checkpoint_id) -> dict`
- `Worker`: `execute(node_config, channel_snapshot) -> WorkerResult`
- `WorkerResult`: `channel_updates: dict`, `interrupt: Optional[interrupt_data]`, `error: Optional[Exception]`

### D5: 数据库设计 — PostgreSQL + UUID + JSONB — Database design

9 张核心表 — 9 core tables (agents, conversations, sessions, messages, tools, knowledge_bases, documents, skills, checkpoints) with complete DDL including indexes.

**理由 — Rationale**: UUID avoids predictable IDs, JSONB for flexible config storage, soft delete for audit trail.

### D6: API 双轨设计 — API dual-track design

OpenAI-compatible layer (`/v1/`): strictly follows OpenAI Chat Completions API spec, supports streaming (SSE), tool calling, function calling, no extension fields.

Hecate Management API (`/api/`): RESTful style, unified error format `{"error": {"code": "...", "message": "..."}}`, Bearer token auth, pagination and filtering.

### D7: LLM 集成 — LiteLLM 封装 — LLM integration

`LLMService` wraps LiteLLM with chat + streaming + tool calling protocol:
1. Agent declares available tools (JSON Schema function definitions)
2. LLM returns `tool_call` (name + arguments)
3. Engine finds and invokes tool
4. Result injected as `tool` role message
5. LLM continues generation

### D8: RAG 管线 — Docling + BGE-M3 + Qdrant — RAG pipeline

Upload → Docling parse (20+ formats) → text chunking (512-1024 tokens, overlap 100-200) → BGE-M3 encode (dense 1024-dim + sparse token weights) → Qdrant upsert (dense + sparse vectors)

Retrieval: Query → BGE-M3 encode → Qdrant hybrid search → Top-K → inject into LLM context

P1: upload, auto-chunking/indexing, hybrid search. P2: bge-reranker-v2-m3 re-ranking, auto-sync, parsing status tracking.

### D9: 安全层 — 双层防护 — Security layer — dual-layer protection

```
User Request → API Layer (NeMo Guardrails outer: dialog flow, topic constraints)
            → Engine Layer (LLM Guard inner: content-level security scanning)
              → Input: PromptInjection, Anonymize, Secrets, Toxicity
              → Output: Sensitive (PII), Toxicity
            → LLM Inference
```

### D10: MCP 集成 — Client-only — MCP integration

P1 MCP Client only: connection config in Tool setup, startup discovery (connect → `tools/list` → sync to Tool table), on-call execution (`tools/call` → result)

### D11: 部署 — Docker Compose — Deployment

Services: hecate-api, postgres:16, qdrant, minio. MinIO for RAG document storage (PDF/Word/PPT → MinIO before Docling parsing).

## Risks / Trade-offs — 风险/权衡

| 风险 — Risk | 影响 — Impact | 缓解 — Mitigation |
|------|------|------|
| Graph DSL compiler complexity | Hard-to-debug compilation errors | Start with simplest Graph (linear chain + conditional branch), iterate |
| LLM Guard performance overhead | 30-200ms per call (CPU) | ONNX Runtime optimization (P2); configurable on/off in P1 |
| BGE-M3 memory usage | FP16 ~1.5GB GPU, CPU ~1.2GB | CPU for dev; single GPU for production |
| PyTorch dependency size | ~2GB install | Pre-built production image; CPU-only torch for dev |
| Chinese PII detection accuracy | Default NER optimized for English | P2 introduce `gyr66/bert-base-chinese-finetuned-ner` |
| LangGraph pattern adaptation | Partial concept mapping | Strict interface boundaries, zero `langchain_core` imports |
| Checkpoint write latency | Synchronous PostgreSQL writes per step | Acceptable in P1 (non-high-frequency); async + cache in P2 |
