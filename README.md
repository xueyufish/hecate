# Hecate

[![CI](https://github.com/xueyufish/hecate/actions/workflows/ci.yml/badge.svg)](https://github.com/xueyufish/hecate/actions/workflows/ci.yml)
[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Status: Alpha](https://img.shields.io/badge/status-alpha-orange)](https://github.com/xueyufish/hecate)
[![Code style: ruff](https://img.shields.io/badge/code%20style-ruff-000000.svg)](https://github.com/xueyufish/hecate/blob/main/pyproject.toml)
[![Type checked: mypy strict](https://img.shields.io/badge/mypy-strict-blue)](https://github.com/xueyufish/hecate/blob/main/pyproject.toml)
[![Pre-commit enabled](https://img.shields.io/badge/pre--commit-enabled-brightgreen?logo=pre-commit)](https://github.com/xueyufish/hecate/blob/main/.pre-commit-config.yaml)

Enterprise-grade, multi-tenant, model-agnostic, MCP-first Agent platform.

Hecate is an enterprise-grade Agent platform with a self-developed Pregel execution runtime. It speaks MCP and A2A natively, integrates 100+ LLMs, and exposes an OpenAI-compatible API so existing tools integrate without change. Multi-agent orchestration, engine-level guardrails, and Docker-isolated sandbox execution are first-class concerns.

> ⚠️ **Hecate is alpha software.** APIs and config schemas may change before 1.0. Pin your version (`hecate==0.1.x`). Report issues → [GitHub Issues](https://github.com/xueyufish/hecate/issues).

## At a glance

![Hecate L1 Architecture](docs/design/images/hecate_l1_architecture.png)

---

## Who is this for?

Hecate is a good fit if you need any of the following:

- **A flexible agent runtime** — code-first Python API for engineers and a visual canvas for non-developers
- **Engine-level extensibility** — 11 core + 4 SPI extension points let you swap schedulers, checkpointers, guardrails
- **Self-hosted on your own infrastructure** — your prompts never leave your network; LLM traffic uses your API keys
- **Multi-agent orchestration with persistence** — graph-based state, durable checkpoints, human-in-the-loop
- **A multi-tenant foundation** — Organization → Workspace → RBAC for an internal agent platform product
- **To study or extend an agent runtime** — layered architecture with a self-developed Pregel engine and no framework lock-in

Hecate is **not** a good fit if you want a managed cloud service — Hecate is OSS, self-hosted, and you run it on your own infrastructure. (Dify or n8n may be better fits if your team is non-developer-first and you want a pure GUI-driven, no-code experience.)

---

## Quick Start

**Pick your path:**

| Goal | Where to start |
|---|---|
| 🧑‍💻 Build an agent runtime (Python API) | [Step 1 → 3](#step-1--start-infrastructure-and-install) below |
| 🎨 Compose agents visually (no code) | After install, open [web/](web/) (the visual canvas) |
| 🔌 Add MCP / A2A integration | [Enable MCP Server](docs/how-to/enable-mcp-server.md) · [Enable A2A Server](docs/how-to/enable-a2a-server.md) |
| 🏢 Deploy to production (K8s / multi-tenant) | [Deploy to production](docs/how-to/deploy-production.md) |

Get Hecate running in **~5 minutes**. **Prerequisites**: Docker, Python 3.12+, [`uv`](https://docs.astral.sh/uv/), and an LLM API key (OpenAI, Anthropic, DeepSeek, Qwen, GLM, Ollama, or any [LiteLLM-supported provider](https://docs.litellm.ai/docs/providers)). **System requirements**: 2 CPU cores, 4 GB RAM (8 GB recommended), 10 GB disk. macOS, Linux, and WSL2 supported; native Windows is experimental. Full guide: [Quickstart](docs/getting-started/quickstart.md).

### Step 1 — Start infrastructure and install

```bash
git clone https://github.com/xueyufish/hecate.git && cd hecate
cp .env.example .env       # required by Docker Compose; add your LLM API key here
docker compose -f docker/docker-compose.yml up -d postgres qdrant minio temporal
uv venv && source .venv/bin/activate && uv pip install -e ".[dev]"
alembic upgrade head

# Sanity-check the setup before starting the server:
hecate preflight
# → [PASS] database: OK
# → [PASS] alembic_head: ...
# → [PASS] env_vars: all present
# → Preflight PASSED.
```

### Step 2 — Start the server

```bash
uvicorn hecate.main:app --reload
```

Open [http://localhost:8000/docs](http://localhost:8000/docs) for the interactive API explorer (Swagger UI) and `/redoc`.

### Step 3 — Send your first chat request

The API is **OpenAI-compatible** — any existing OpenAI client works by pointing `base_url` at Hecate:

```bash
curl -X POST http://localhost:8000/v1/chat/completions \
  -H "Authorization: Bearer $(grep ^OPENAI_API_KEY .env | cut -d= -f2)" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gpt-4o",
    "messages": [{"role": "user", "content": "Hello!"}]
  }'
```

**Expected response**: a JSON object with `choices[0].message.content` containing a greeting. If you see that, Hecate is running.

For streaming responses (SSE), add `"stream": true`:

```bash
curl -N -X POST http://localhost:8000/v1/chat/completions \
  -H "Authorization: Bearer $(grep ^OPENAI_API_KEY .env | cut -d= -f2)" \
  -H "Content-Type: application/json" \
  -d '{"model": "gpt-4o", "stream": true, "messages": [{"role": "user", "content": "Hello!"}]}'
```

`-N` disables curl buffering; tokens stream as they are generated.

### Step 4 — Or use any OpenAI client (drop-in)

Hecate's `/v1/chat/completions` is wire-compatible with OpenAI. Any existing OpenAI client works by pointing `base_url` at Hecate — no code rewrite:

```python
from openai import OpenAI

api_key = next(
    line.split("=", 1)[1].strip()
    for line in open(".env")
    if line.startswith("OPENAI_API_KEY=")
)

client = OpenAI(
    base_url="http://localhost:8000/v1",
    api_key=api_key,
)

resp = client.chat.completions.create(
    model="gpt-4o",
    messages=[{"role": "user", "content": "Hello!"}],
)
print(resp.choices[0].message.content)
```

Also works with `litellm`, `langchain-openai`, `instructor`, `vllm`, `llama-index` — any client that speaks OpenAI's wire protocol.

### Troubleshooting

| Symptom | First thing to check |
|---|---|
| `hecate preflight` reports `database: FAIL` | `docker compose -f docker/docker-compose.yml ps` — is Postgres up? |
| `curl` returns `401 Unauthorized` | `cat .env` — is `OPENAI_API_KEY=` set (and uncommented)? |
| `curl` returns `502 Bad Gateway` | `docker compose logs hecate` — the server may still be starting |
| Port `8000` already in use | `lsof -i :8000` then `kill <pid>`, or run uvicorn on `--port 8080` |
| Anything else | [Full troubleshooting guide](docs/how-to/troubleshoot.md) |

### What's next

**Tutorials** — feature-focused walkthroughs:

- [Build your first agent](docs/tutorials/01-first-agent.md) — code-first Python walkthrough
- [Add a knowledge base](docs/tutorials/02-knowledge-base.md) — RAG in 10 minutes
- [Connect an MCP server](docs/tutorials/03-mcp-integration.md) — extend with external tools
- [A2A Protocol](docs/tutorials/09-a2a-protocol.md) — cross-agent orchestration
- [OpenAI SDK compatibility](docs/tutorials/10-openai-compatibility.md) — use Hecate as a drop-in for OpenAI
- [Visual canvas](docs/tutorials/11-visual-canvas.md) — drag-and-drop workflow design

**Use cases** — end-to-end business scenarios:

- [Customer support bot](docs/use-cases/01-customer-support-bot.md) — RAG + Guardrails + HITL
- [Code review agent](docs/use-cases/02-code-review-agent.md) — MCP + Multi-Agent
- [Research team](docs/use-cases/03-research-team.md) — Multi-Agent + Streaming + Evaluation

---

## Features

- **Graph-First Engine** — Self-built Pregel/BSP runtime with 11 core + 4 SPI extension points. Zero external framework dependencies for the engine.
- **A2A Protocol Native** — Linux Foundation v1.0 GA — signed AgentCards (`/.well-known/agent-card.json`), JSON-RPC 2.0 task lifecycle, SSE streaming, and JWS+RFC 8785 trust model. Operates as both A2A server and A2A client.
- **MCP Native** — Bidirectional Model Context Protocol: Hecate consumes external MCP servers (GitHub, Slack, etc.) and exposes its own as a server (Streamable HTTP transport).
- **OpenAI SDK Drop-in** — Wire-compatible `/v1/chat/completions` endpoint. Any OpenAI client (Python, JS, litellm, langchain-openai, instructor, vllm) works against Hecate by changing `base_url`.
- **Visual Canvas** — Drag-and-drop workflow editor in `web/` (React Flow + Next.js). Bidirectional sync with the JSON graph DSL — what you build visually is the same code-defined workflow.
- **Multi-Agent Orchestration** — Six collaboration patterns (Hierarchical, Handoff, Pipeline, Broadcast, Negotiation, Debate) unified as Graph templates.
- **Context Engineering** — An extensible pipeline (assembler, evidence tracker, phase detector, token budget, provider shaping, message prioritization, tool filtering, offloader) that keeps long-running agents on-budget and on-task.
- **Multi-Tenant** — Organization → Workspace → RBAC with `workspace_id` on 34 data models for tenant isolation. SSO via OIDC/SAML/LDAP, SCIM v2 provisioning.
- **Plugin System** — 6 plugin types (Tool / Evaluator / Channel / Auth / Notifier / Extension) with hot-reload, declared permissions, and versioned manifests. Plus Core extension points for engine-internal customization.
- **IM Channels** — Reach Hecate agents from Feishu (Lark) and Slack via inbound webhooks. Mandatory Bound Identity model ensures every IM user is bound to a Hecate user before any conversation starts. Same Hecate user shares one conversation thread across both channels. See [Configure Feishu and Slack](docs/how-to/configure-feishu-slack.md) and the [IM channel architecture overview](docs/concepts/im-channel-architecture.md).
- **Engine-Level Guardrails** — Four hook types (Pre/Post LLM/Tool) at every LLM and Tool boundary; the same hooks power PII masking, audit logging, and human-in-the-loop flows.
- **OpenSpec Workflow** — Every feature shipped through structured proposal → design → specs → implementation → archive (similar to Python PEPs / Kubernetes KEPs / Rust RFCs). 30 ADRs and 100+ archived changes document the architecture.

---

## Engineering Approach

Hecate's design follows three disciplines common to serious agent platforms:

- **Harness engineering** — the runtime is the harness. Every LLM call passes through the Pregel superstep loop, with durable checkpoints, retry policies, and 11 core + 4 SPI extension points providing observability and control at every boundary.
- **Loop engineering** — agent control loops are first-class. The superstep iteration is complemented by `interrupt()`/`Command()` for human-in-the-loop, `RetryStrategy` for failure recovery, and multi-agent delegation patterns where each subgraph runs its own execution loop.
- **Graph engineering** — workflows are graphs. A JSON DSL describes nodes and edges; the compiler validates, optimizes, and emits an executable `CompiledGraph`. Six multi-agent collaboration patterns ship as static graph templates.

---

## Trust & Security

Built for on-premises and regulated deployments:

- **PII masking and data isolation** — guardrail hooks redact sensitive content before it leaves your network
- **Audit trail** — every LLM call, tool invocation, and checkpoint is logged to your own PostgreSQL
- **Sandboxed tool execution** — Docker-isolated runtime with explicit permission scopes per agent
- **No external data retention** — prompts and completions go directly to your LLM provider; Hecate does not store them

---

## CLI Tools

Hecate ships two console-script entry points:

- **`hecate`** — the main CLI for managing agents, sessions, knowledge bases, workflows, and other resources. See [`docs/reference/cli.md`](docs/reference/cli.md) for the full command list.
- **`hecate-migrate`** — standalone migration runner. Designed for one-shot use as a Docker Compose init service, a Kubernetes init container, or a Helm pre-install hook — runs Alembic migrations without booting the full web application.

After `uv pip install -e ".[dev]"`, both commands are available on your `PATH`.

---

## Documentation

- **[Getting Started](docs/getting-started/)** — install Hecate locally and send your first chat request in ~5 minutes.
- **[Tutorials](docs/tutorials/)** — 11 end-to-end tutorials (first agent, knowledge base, MCP, multi-agent, A2A, OpenAI SDK, visual canvas, plus use cases).
- **[How-to Guides](docs/how-to/)** — 16 task-oriented recipes (LLM providers, deployment, MCP, A2A, SSO, backups, webhooks, troubleshooting).
- **[Concepts](docs/concepts/)** — 23 explanatory articles that help you understand Hecate's core ideas before building.
- **[Reference](docs/reference/)** — REST API, CLI, Graph DSL, plugin manifest, event catalog, extension points, data models.
- **[Architecture Center](docs/design/)** — 13 architecture deep dives + 30 ADRs + ADR index, plus strategy docs (positioning).
- **[Use Cases](docs/use-cases/)** — end-to-end business scenarios (customer support bot, code review agent, research team).
- **[Migrations](docs/migrations/)** — schema migration guides (expand-contract pattern, 0.1 → 0.2 upgrade).
- **[Operations](docs/operations/)** — runbooks for production (health checks, backup, rollback, log analysis, performance).
- **[About](docs/about/)** — inspired by, license, contributors, release notes.
- **[CHANGELOG.md](CHANGELOG.md)** — machine-readable changelog.
- **[GitHub Releases](https://github.com/xueyufish/hecate/releases)** — full release history with auto-generated notes.

---

## Inspired by

Hecate builds on the shoulders of giants: [LangGraph](https://github.com/langchain-ai/langgraph), [Model Context Protocol](https://modelcontextprotocol.io/), [A2A Protocol](https://a2a-protocol.org/), [FastAPI](https://fastapi.tiangolo.com/), [Pydantic](https://docs.pydantic.dev/), [SQLAlchemy](https://www.sqlalchemy.org/), [LiteLLM](https://github.com/BerriAI/litellm). Full credits and specific inspirations are in [docs/about/inspired-by.md](docs/about/inspired-by.md).

---

## Contributing

Contributions are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md) for the workflow, coding conventions, and how to file issues. Every feature ships through the [OpenSpec](openspec/) workflow with requirements, scenarios, and design docs.

---

## License

[MIT](LICENSE)