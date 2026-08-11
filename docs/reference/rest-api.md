# REST API Reference

Hecate exposes four API surfaces: an OpenAI-compatible chat surface, a management surface, identity/protocol surfaces (SSO, SCIM, MCP, A2A), and system endpoints. This page is a route map — for per-endpoint parameters, request/response schemas, and live "Try it out", use the **interactive Swagger UI at `/docs`** (or ReDoc at `/redoc`) on a running server.

All routes are registered in `src/hecate/main.py`. Prefixes come from either the `include_router(..., prefix=...)` call, the router's own `APIRouter(prefix=...)`, or both (they stack).

---

## Authentication

| Surface | Auth |
|---------|------|
| `/v1/*` (OpenAI-compatible) | Bearer API key in the `Authorization` header — `HECATE_API_KEYS` |
| `/api/*` (management) | API key or session/JWT depending on the route |
| `/auth/sso`, `/scim/v2/*` | Federated (OIDC/SAML/LDAP) or SCIM bearer |
| `/health/*`, `/version`, `/metrics` | None (intentional — for probes and scrapers) |

Errors use a unified shape: `{"error": {"code": "...", "message": "...", "details": null}}`. Status codes follow the table in the global exception handler (400 validation, 401 auth, 404 not found, 422 request validation, 429 rate limit, 500 internal).

---

## OpenAI-compatible surface (`/v1`)

A drop-in replacement for the OpenAI Chat Completions and Models APIs. Point any OpenAI-compatible client at `http://your-host:8000/v1` with a Hecate API key.

| Route | Method | Purpose |
|-------|--------|---------|
| `/v1/chat/completions` | POST | Chat completion — resolves an OpenAI model name **or** `agent/<AGENT_ID>` to a configured agent and runs it through the Pregel runtime |
| `/v1/models` | GET | List available models (provider models + registered agents) |

See [Quickstart Step 6–8](../getting-started/quickstart.md#step-6--send-your-first-chat-request) for worked examples.

---

## Management surface (`/api`)

The management API is organized by functional area. Each group below lists the base path and the resource it manages.

### Agents and execution

| Base path | Resource |
|-----------|----------|
| `/api/agents` | Agent CRUD, persona, model config, tools, knowledge bindings, risk level |
| `/api/agents/{agent_id}/environment` | AgentEnvironment filesystem (working files, offloaded context) |
| `/api/sessions` | Conversation sessions and lifecycle (`active` / `interrupted` / `completed`) |
| `/api/conversations` | Conversation threads within sessions |
| `/api/messages` | Individual messages |
| `/api/workflows` | Workflow graph definitions (JSON DSL), versioning, publish |
| `/api/orchestration-templates` | Reusable multi-agent orchestration templates |
| `/api/collaboration-patterns` | The six collaboration patterns (Hierarchical, Handoff, Pipeline, Broadcast, Negotiation, Debate) |
| `/api/agent-templates` | Preset agent templates |
| `/api/schedules` | Scheduled / recurring agent runs |
| `/api/preflight` | Pre-execution validation checks |

### Tools and skills

| Base path | Resource |
|-----------|----------|
| `/api/tools` | Tool CRUD (built-in, custom, MCP-discovered) |
| `/api/tools/cache` | Tool-result cache management |
| `/api/tool-policies` | Per-workspace and per-agent tool permission policies |
| `/api/skills` | Skills (SKILL.md format) and the skill registry |
| `/api/plugins` | Discovered and registered plugins |

### Knowledge and memory

| Base path | Resource |
|-----------|----------|
| `/api/knowledge-bases` | Knowledge bases, document upload, chunking/embedding config |
| `/api/agents/{id}/memory-blocks` | L1 working-memory blocks (named regions in the context window) |
| `/api/memory` | L3 user memories — list, create, delete (workspace-scoped) |
| `/api/users/{user_id}/memories` | Per-user L3 memories — list and semantic search |
| `/api/agents/{id}/knowledge` | L4 knowledge memories — insert, list, search (hybrid dense+sparse) |
| `/api/sessions/{id}/compression` | L2 conversation compression status (snip / microcompact / autocompact) |

See [Manage agent memory](../how-to/manage-agent-memory.md) for operational recipes across all four levels.

### Models and inference

| Base path | Resource |
|-----------|----------|
| `/api/model-providers` | LLM provider connections and credentials |
| `/api/models` | Model lifecycle management |
| `/api/models/catalog` | Model catalog |
| `/api/models/cost` | Cost management and accounting |
| `/api/model-pricing` | Per-model pricing data |
| `/api/inference` | Inference endpoints and parameters |
| `/api/fine-tuning` | Fine-tuning jobs |

### Guardrails and security

| Base path | Resource |
|-----------|----------|
| `/api/hooks` | Guardrail hook configuration (Pre/Post LLM/Tool) |
| `/api/security` | `ToolDecision` records and `SecurityFinding` records (tool access allow/deny/require_approval, policy-violation findings) |
| `/api/security/findings/{id}/feedback` | Record true/false-positive feedback on a finding — used to tune DLP and other detectors |
| `/api/audit` | Structured audit trail (every HTTP request, written asynchronously to PostgreSQL) |

The DLP engine (see [DLP](../concepts/dlp.md)) runs inside the security hooks and the MCP egress filter, writing `dlp:`-prefixed findings. The findings query and feedback endpoints above are live; DLP policy CRUD endpoints (`/dlp/policies`, `/dlp/custom-regex`, `/dlp/dictionaries`, `/dlp/scan/test`) are implemented in `api/management/dlp.py` but not yet mounted.

### Organizations and tenancy

| Base path | Resource |
|-----------|----------|
| `/api/orgs` | Organizations (top-level tenant boundary) |
| `/api/orgs/{org_id}/workspaces` | Workspaces (unit of isolation) under an org |
| `/api/workspace-members` | Workspace membership and roles |
| `/api/api-keys` | API key management |
| `/api/quotas` | Per-tenant quotas (`QUOTA_CACHE_TTL` governs the cache) |
| `/api/feature-flags` | Feature flags — toggle or transition to `retired` (used by the [Rollback Runbook](../operations/rollback.md#path-3-feature-flag-rollback)) |

### Ops center, monitoring, and cost

| Base path | Resource |
|-----------|----------|
| `/api/ops-center` | Ops-center overview dashboard |
| `/api/ops-center/agents` | Agent health monitoring |
| `/api/ops-center/conversations` | Conversation analytics |
| `/api/ops-center/tools` | Tool analytics |
| `/api/traces` | Distributed traces (OpenTelemetry span export, when `TRACE_DB_EXPORT_ENABLED`) |
| `/api/monitoring` | Monitoring service data |
| `/api/monitoring/models` | Model-specific monitoring |
| `/api/budgets` | Budget governance and forecast snapshots |
| `/api/costs` | Cost records |
| `/api/alerts` | Alert rules, events, silences, channels, escalation policies |

### Protocols (management side)

| Base path | Resource |
|-----------|----------|
| `/api/mcp` | MCP client/server configuration management |
| `/api/a2a` | A2A agent registration and discovery management |
| `/api/i18n` | Internationalization / localization |

### System and backup

| Base path | Resource |
|-----------|----------|
| `/api/system/backups` | Create, list, inspect, and verify backups (see [Backup and Restore Runbook](../operations/backup-restore.md)) |
| `/api/system/restore` | Restore from a backup (requires `confirm: true`) |

### Evaluation

| Base path | Resource |
|-----------|----------|
| `/api/evaluation/datasets` | Evaluation datasets — create, list, update, soft-delete (workspace-scoped) |
| `/api/evaluation/datasets/{id}/items` | Test-case items inside a dataset — add, list, delete |
| `/api/evaluation/runs` | Create + execute a run (selects evaluators, grades items, returns scores immediately), list runs |
| `/api/evaluation/runs/{id}` | Run summary with `metric_averages` and totals |
| `/api/evaluation/runs/{id}/scores` | Per-item, per-metric scores with `reasoning` |

Built-in evaluators: `correctness`, `relevancy`, `completeness`, `tool_call_accuracy`, `task_completion` (always); `context_precision`, `context_recall`, `faithfulness`, `answer_relevancy` (RAG, require `ragas`). See [Agent Evaluation](../concepts/evaluation.md) and the [Evaluate an Agent tutorial](../tutorials/08-agent-evaluation.md).

---

## Identity and federation surfaces

| Base path | Purpose |
|-----------|---------|
| `/auth` | Authentication (login, token issue/refresh) |
| `/auth/sso` | SSO endpoints — OIDC, SAML, LDAP sign-in flows |
| `/scim/v2` | SCIM v2 service-provider discovery |
| `/scim/v2/Users` | SCIM user provisioning |
| `/scim/v2/Groups` | SCIM group provisioning |

See [Configure SSO and SCIM](../how-to/configure-sso-scim.md) for wiring identity providers.

---

## Protocol server surfaces (conditional)

These mount only when the corresponding flag is enabled in `.env`:

| Surface | Mount condition | Purpose |
|---------|-----------------|---------|
| **MCP Server** | `MCP_SERVER_ENABLED=true` | Exposes Hecate agents, knowledge bases, and tools as MCP primitives at `/mcp` (Streamable HTTP transport) |
| **A2A Server** | `A2A_SERVER_ENABLED=true` | Exposes Hecate agents via the Agent-to-Agent protocol; discovery via `/.well-known/agent.json` (Agent Cards) |

See [Enable MCP Server](../how-to/enable-mcp-server.md) and [Enable A2A Server](../how-to/enable-a2a-server.md).

---

## System endpoints

These live directly on the application (not under `/api`) and are documented in the [Health Checks Runbook](../operations/health-checks.md):

| Route | Method | Purpose |
|-------|--------|---------|
| `/health/live` | GET | Liveness probe — process is alive |
| `/health/ready` | GET | Readiness probe — DB + Redis + Qdrant checks, 503 if draining or unhealthy |
| `/health/startup` | GET | Startup probe — lifespan initialization complete |
| `/version` | GET | Build info: version, commit, alembic head, Python version, build date |
| `/metrics` | GET | Prometheus metrics (text exposition format) |
| `/docs` | GET | Swagger UI (interactive API docs) |
| `/redoc` | GET | ReDoc (alternative API docs UI) |
| `/openapi.json` | GET | Raw OpenAPI schema |

---

## Interactive docs

The authoritative, always-up-to-date reference is the OpenAPI schema Hecate auto-generates from its FastAPI routers. When the server is running:

- **Swagger UI** — `http://localhost:8000/docs` — every endpoint with parameters, schemas, and a "Try it out" button.
- **ReDoc** — `http://localhost:8000/redoc` — a cleaner read-only view of the same schema.
- **Raw schema** — `http://localhost:8000/openapi.json` — for code-generation or importing into API tools.

This page intentionally stays at the route-map level so it does not drift from the code. For the exact parameters and response shape of any endpoint, defer to the interactive docs.
