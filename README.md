# Hecate

[![CI](https://github.com/xueyufish/hecate/actions/workflows/ci.yml/badge.svg)](https://github.com/xueyufish/hecate/actions/workflows/ci.yml)
[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Conventional Commits](https://img.shields.io/badge/Conventional%20Commits-1.0.0-fe5196.svg)](https://conventionalcommits.org)

**Enterprise-grade, multi-tenant, model-agnostic, MCP-first Agent platform.**

Build, orchestrate, and run AI Agent applications on cloud SaaS or self-hosted infrastructure — no vendor lock-in.

---

## Highlights

- **Graph-First Engine** — Self-built Pregel runtime with JSON DSL, compiler, and channel system. Zero external framework dependencies (not even LangChain).
- **15 Extension Points** — 11 Core extension points (scheduling, eviction, optimization, conflict resolution, event sourcing, context engine, guardrails) + 4 SPI extension points (evaluator, channel, auth provider, notifier).
- **Visual Canvas** — React Flow-based drag-and-drop workflow builder with 6 multi-agent collaboration patterns, typed edges, and fan-out/merge nodes.
- **Multi-Agent Orchestration** — Hierarchical, Handoff, Pipeline, Broadcast, Negotiation, and Debate patterns — all unified as Graph templates.
- **MCP Bidirectional** — Native MCP Client (consume external tools) + MCP Server (expose Hecate as tool provider).
- **Model-Agnostic** — 100+ LLM providers via LiteLLM with intelligent routing, circuit breaker, and A/B testing.
- **Multi-Tenant** — Organization → Workspace → RBAC with data-level tenant isolation across 15 models.
- **Enterprise Security** — Engine-level guardrail hooks (Pre/Post LLM/Tool), PII masking with encryption, audit trail, and Docker sandbox execution.
- **Context Engineering** — 6-component pipeline: assembler, evidence tracker, phase detection, token budget governance, provider shaping, and message prioritization.
- **Spec-Driven Development** — 86 feature specs + 62 completed change proposals via OpenSpec workflow. Every feature has requirements, scenarios, and design docs.

## Project Stats

| Metric | Value |
|--------|-------|
| Features (P1–P5) | 343 total (123 implemented) |
| Tests | 1,700+ |
| Extension Points | 15 (11 Core + 4 SPI) |
| OpenSpec specs | 86 |
| Completed changes | 62 |
| LLM Providers | 100+ via LiteLLM |
| Database Backends | PostgreSQL, MySQL, SQLite |
| Vector DB Backends | Qdrant, Chroma |
| Python | 3.12+ |

## Architecture

![Hecate L1 Architecture](docs/design/images/hecate_l1_architecture.png)

> **Legend**: ✅ Green = Implemented | 📋 Yellow dashed = Planned

## Quick Start

### Prerequisites

- Python 3.12+
- PostgreSQL 16
- Qdrant (vector database)
- MinIO (object storage)

> **Note:** MySQL and SQLite are also supported as database backends. PostgreSQL is recommended for production and is the default in Docker Compose.

### Installation

```bash
# Clone the repository
git clone https://github.com/xueyufish/hecate.git
cd hecate

# Create virtual environment and install dependencies
uv venv
source .venv/bin/activate
uv pip install -e ".[dev]"

# Set up environment
cp .env.example .env
# Edit .env with your configuration

# Run database migrations
alembic upgrade head

# Start the server
uvicorn hecate.main:app --reload
```

### Docker Compose

```bash
# Start infrastructure (PostgreSQL, Qdrant, MinIO)
docker compose -f docker/docker-compose.yml up -d

# Then install and run as above
```

## API Overview

### OpenAI-Compatible Endpoints

```bash
# Chat completion (drop-in replacement)
curl -X POST http://localhost:8000/v1/chat/completions \
  -H "Authorization: Bearer your-api-key" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gpt-4o",
    "messages": [{"role": "user", "content": "Hello!"}]
  }'

# List models
curl http://localhost:8000/v1/models \
  -H "Authorization: Bearer your-api-key"
```

### Management API

```bash
# Create agent
curl -X POST http://localhost:8000/api/agents \
  -H "Authorization: Bearer your-api-key" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "My Agent",
    "model_config": {"model": "gpt-4o"},
    "mode": "chat"
  }'

# Create knowledge base
curl -X POST http://localhost:8000/api/knowledge-bases \
  -H "Authorization: Bearer your-api-key" \
  -H "Content-Type: application/json" \
  -d '{"name": "My KB", "description": "Knowledge base for docs"}'

# Upload document
curl -X POST http://localhost:8000/api/knowledge-bases/{kb_id}/documents \
  -H "Authorization: Bearer your-api-key" \
  -F "file=@document.pdf"
```

## Configuration

Key environment variables (see `.env.example` for full list):

| Variable | Description | Default |
|----------|-------------|---------|
| `DATABASE_URL` | Database connection string | `postgresql+asyncpg://hecate:hecate@localhost:5432/hecate` |
| `QDRANT_URL` | Qdrant endpoint | `http://localhost:6333` |
| `MINIO_URL` | MinIO endpoint | `localhost:9000` |
| `HECATE_API_KEYS` | Comma-separated API keys | — |
| `LLM_GUARD_ENABLED` | Enable prompt/output scanning | `true` |
| `RATE_LIMIT_RPM` | Requests per minute per key | `60` |

## Development

```bash
# Run all tests (1,700+ tests)
python -m pytest tests/ -q

# Run a specific test file
python -m pytest tests/test_engine/test_pregel.py -v

# Lint
ruff check src/hecate/ tests/

# Format check
ruff format --check src/ tests/

# Type check
mypy src/

# All four checks (also runs as pre-commit hook)
ruff check src/hecate/ tests/ && ruff format --check src/ tests/ && mypy src/ && python -m pytest tests/ -q
```

## Engine Layer Design

The execution engine is Hecate's core differentiator — a self-built Pregel runtime with zero external framework dependencies:

```
JSON DSL → Compiler (schema validation + optimization passes) → CompiledGraph
                                                                    │
                                                                    ▼
                                                           Pregel Runtime
                                                    (BSP superstep loop)
                                                    │               │
                                         Channel System      Worker Pool
                                         (4 types +          (thread pool →
                                          pluggable registry)  cross-process)
                                                    │
                                         Checkpoint Store
                                         (PostgreSQL + memory cache)
```

**15 Extension Points** enable pluggable extensibility — 11 Core + 4 SPI:

**Core Extension Points (11)**:

| Extension Point | Purpose |
|-----|---------|
| `EnginePort` | Service-to-engine adapter (LLM, tools, knowledge, checkpoint) |
| `Worker` / `WorkerPool` | Node execution dispatch |
| `CheckpointStore` | State persistence and recovery |
| `EventStore` | Append-only event logging with replay |
| `ContextEngine` | Message selection, compression, token estimation |
| `SchedulerStrategy` | Node scheduling (FIFO default, pluggable) |
| `EvictionPolicy` | Channel memory management |
| `OptimizationPass` | Graph optimization (dead node elimination, parallel detection) |
| `ConflictResolver` | Concurrent channel update resolution |
| `Guardrail Hooks (×4)` | Pre/Post LLM/Tool interception |

**SPI Extension Points (4)** — 🔌 Planned, within the Engine layer:

| Extension Point | Purpose |
|-----|---------|
| `Evaluator` | Evaluator interface; 40+ built-in evaluators |
| `Channel` | Channel adapter; REST/WS/CLI built-in |
| `AuthProvider` | Auth provider; JWT/APIKey built-in |
| `Notifier` | Notifier; Email/Webhook built-in |

## Documentation

| Document | Location | Description |
|----------|----------|-------------|
| Architecture Design | `docs/design/architecture.md` | 5-layer architecture, 28 ADRs, engine design |
| OpenSpec Specs | `openspec/specs/` | 86 feature specifications with requirements and scenarios |
| OpenSpec Archive | `openspec/changes/archive/` | 62 completed change proposals with design docs |
| Graph DSL Schema | `src/hecate/engine/graph-dsl.schema.json` | JSON Schema for graph definition |

## License

[MIT](LICENSE)
