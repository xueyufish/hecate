# Quick Reference

One page for everything you look up frequently. Bookmark this.

> For full details on any topic, follow the linked deep-dive. This page intentionally stays at the cheat-sheet level.

---

## API surfaces

| Surface | Prefix | Auth | Key endpoints |
|---------|--------|------|---------------|
| OpenAI-compatible | `/v1` | Bearer API key | `POST /v1/chat/completions`, `GET /v1/models` |
| Management | `/api` | API key or JWT | `/api/agents`, `/api/sessions`, `/api/tools`, `/api/knowledge-bases`, `/api/workflows` |
| Identity | `/auth`, `/scim/v2` | Federated or SCIM bearer | `/auth/login`, `/auth/sso/*`, `/scim/v2/Users` |
| System | — | None | `/health/live`, `/health/ready`, `/metrics`, `/version`, `/docs` |
| MCP Server | `/mcp` | Conditional | Only when `MCP_SERVER_ENABLED=true` |
| A2A Server | `/.well-known/agent-card.json`, `/a2a/` | Conditional | Only when `A2A_SERVER_ENABLED=true` |

Full route map: [REST API](rest-api.md). Interactive: `/docs` (Swagger) or `/redoc`.

---

## CLI commands

```bash
hecate agent create | list | get | update | delete
hecate session create | list | resume
hecate chat "your message"          # one-shot
hecate chat -i                      # interactive with streaming
hecate kb create | upload | list
hecate tool list | get
hecate workflow create | validate | test
hecate auth login | whoami
hecate --json ...                   # machine-readable output
hecate --profile prod ...           # named profile from ~/.hecate/config.toml

hecate-migrate                      # runs alembic upgrade head (init container safe)
```

Full reference: [CLI](cli.md).

---

## Docker services and ports

| Service | Port | Purpose |
|---------|------|---------|
| Hecate app | `8000` | API server |
| PostgreSQL | `5432` | Primary database (required) |
| Qdrant | `6333` (HTTP), `6334` (gRPC) | Vector store (required for RAG) |
| MinIO | `9000` (API), `9001` (Console) | Object storage (required for RAG) |
| Temporal | `7233` | Durable execution (optional) |
| Temporal UI | `8080` | Web dashboard for Temporal (optional) |

---

## Critical environment variables

| Category | Key vars |
|----------|----------|
| Database | `DATABASE_URL` |
| LLM | `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `DEEPSEEK_API_KEY`, `DASHSCOPE_API_KEY`, `ZAI_API_KEY` |
| Security | `HECATE_API_KEYS`, `JWT_SECRET`, `LLM_GUARD_ENABLED`, `RATE_LIMIT_RPM` |
| Vector store | `VECTOR_STORE_TYPE` (qdrant/chroma), `QDRANT_URL` |
| Object storage | `MINIO_URL`, `MINIO_ACCESS_KEY`, `MINIO_SECRET_KEY` |
| DLP | `DLP_ENABLED`, `DLP_INPUT_HOOK_ENABLED`, `DLP_OUTPUT_HOOK_ENABLED` |
| Tracing | `TRACING_ENABLED`, `TRACE_DB_EXPORT_ENABLED`, `OTEL_EXPORTER_OTLP_ENDPOINT` |
| Protocols | `MCP_SERVER_ENABLED`, `A2A_SERVER_ENABLED` |
| Session state | `SESSION_STATE_STORE_BACKEND` (memory/redis/postgres/tiered) |

Full list with defaults: [Environment Variables](env-vars.md).

---

## Execution modes

| Mode | Structure | One-liner |
|------|-----------|-----------|
| `chat` | Single LLM call | Direct Q&A, default |
| `three_layer` | Guard → Plan → Execute | Built-in safety + planning |
| `workflow` | Custom graph (JSON DSL) | Multi-agent, branching, parallel |

See [Agents and Execution Modes](../concepts/agents.md).

---

## Graph DSL: node and channel types

**9 node types**: `conversation`, `tool-call`, `condition`, `agent`, `knowledge-retrieval`, `variable-set`, `suggestion`, `fan-out`, `merge`

**4 channel types**: `last_value` (overwrite), `topic` (append), `accumulator` (reduce), `persistent_topic` (deprecated → use `topic` + `persistent: true`)

Full spec: [Graph DSL](graph-dsl.md). Concept: [Workflows](../concepts/workflows.md).

---

## Four-level memory

| Level | Name | Scope | Persists in |
|-------|------|-------|-------------|
| L1 | Working memory | Single execution | Context window (ephemeral) |
| L2 | Conversation memory | Single session | Compressed + checkpointed |
| L3 | User memory | Cross-session, per user | Database (embeddings) |
| L4 | Knowledge memory | Workspace-wide | Vector store (RAG) |

See [Memory System](../concepts/memory.md).

---

## Guardrail hooks and tool risk

**4 hook types**: `PreLLMHook` (before LLM), `PostLLMHook` (after LLM), `PreToolHook` (before tool), `PostToolHook` (after tool)

**4 risk levels**: `LOW` → `MEDIUM` → `HIGH` → `CRITICAL`

**4 approval scopes**: `once`, `session`, `project`, `global`

See [Guardrails and Hooks](../concepts/guardrails.md).

---

## Built-in tools

| Tool | Risk | Sandbox? |
|------|------|---------|
| `web_search` | LOW | No |
| `read_file` | LOW | No |
| `write_file` | MEDIUM | No |
| `list_files` | LOW | No |
| `execute_code` | HIGH | Yes (Docker pool) |

See [Tools, MCP, and A2A](../concepts/tools-and-mcp.md).

---

## Routing strategies

| Strategy | Picks by |
|----------|---------|
| `COST` | Cheapest model meeting constraints |
| `LATENCY` | Fastest model |
| `CAPABILITY` | Most capable model |
| `BALANCED` | Weighted score (default) |

See [Model Hub](../concepts/model-hub.md).

---

## Key file locations

| What | Where |
|------|-------|
| Engine (Pregel runtime, compiler, channels) | `src/hecate/engine/` |
| Services (LLM, RAG, tools, MCP, auth) | `src/hecate/services/` |
| API routes | `src/hecate/api/` |
| ORM models | `src/hecate/models/` |
| Configuration | `src/hecate/core/config.py` |
| Graph DSL JSON Schema | `src/hecate/engine/graph-dsl.schema.json` |
| App entrypoint | `src/hecate/main.py` |
| Tests | `tests/` (mirrors `src/hecate/` structure) |

---

## Authentication credentials

| Type | Format | Scope |
|------|--------|-------|
| API Key | `hcat_*` (SHA-256 hashed) | `SYSTEM` (cross-org) or `WORKSPACE` |
| JWT Access | `eyJ...` (short-lived) | Carries user_id, org_id, workspace_id, role |
| JWT Refresh | `eyJ...` (long-lived) | Exchange for new access token |
| SSO | OIDC / SAML / LDAP | JIT provisions `UserModel` with `sso_id` |

See [Authentication and Identity](../concepts/auth-identity.md).

---

## Observability signals

| Signal | Endpoint / Export | Storage |
|--------|-------------------|---------|
| Traces | OTel SDK → Postgres + external | `traces` table + Tempo/Jaeger/etc. |
| Metrics | `GET /metrics` (Prometheus) | In-memory or TimescaleDB |
| Logs | stdout (JSON via `StructuredLogger`) | ELK / Loki / Fluent Bit |
| Audit | SIEM pipeline (Webhook/Syslog/OCSF) | PostgreSQL |

See [Observability](../concepts/observability.md).
