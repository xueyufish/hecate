# Hecate Agent Platform

Enterprise-grade, self-hosted, model-agnostic, MCP-first Agent platform.

## Features

- **Graph-based Agent Orchestration** — Define agents as directed graphs with Guard → Planner → Sub-Agent architecture
- **OpenAI-Compatible API** — Drop-in replacement for OpenAI's Chat Completions API
- **Model-Agnostic** — Support for OpenAI, Anthropic, and 100+ LLM providers via LiteLLM
- **RAG Pipeline** — Document ingestion, chunking, embedding, and hybrid search
- **Security** — LLM Guard for prompt/output scanning, NeMo Guardrails for conversation control
- **MCP Integration** — Native Model Context Protocol support for tool discovery and execution
- **Checkpoint System** — Pause and resume agent execution with full state preservation

## Quick Start

### Prerequisites

- Python 3.12+
- PostgreSQL 16
- Qdrant (vector database)
- MinIO (object storage)

### Installation

```bash
# Clone the repository
git clone https://github.com/your-org/hecate.git
cd hecate

# Create virtual environment and install dependencies
uv venv
source .venv/bin/activate
uv pip install -e ".[dev]"

# Or use the lock file for exact versions
uv pip sync uv.lock

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
docker compose -f docker/docker-compose.yml up -d
```

## API Overview

### OpenAI Compatible Endpoints

```bash
# Chat completion
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

# List agents
curl http://localhost:8000/api/agents \
  -H "Authorization: Bearer your-api-key"

# Create knowledge base
curl -X POST http://localhost:8000/api/knowledge-bases \
  -H "Authorization: Bearer your-api-key" \
  -H "Content-Type: application/json" \
  -d '{"name": "My KB", "description": "Knowledge base for docs"}'
```

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                        API Layer                            │
│  /v1/chat/completions  │  /api/agents  │  /api/sessions    │
└─────────────────────────┬───────────────────────────────────┘
                          │
┌─────────────────────────▼───────────────────────────────────┐
│                    Service Layer                            │
│  LLM Service  │  RAG Pipeline  │  Security  │  MCP Client  │
└─────────────────────────┬───────────────────────────────────┘
                          │
┌─────────────────────────▼───────────────────────────────────┐
│                   Engine Layer                              │
│  Graph DSL  │  Compiler  │  Pregel Runtime  │  Channels    │
└─────────────────────────┬───────────────────────────────────┘
                          │
┌─────────────────────────▼───────────────────────────────────┐
│                 Infrastructure                              │
│  PostgreSQL  │  Qdrant  │  MinIO  │  LiteLLM              │
└─────────────────────────────────────────────────────────────┘
```

## Configuration

Environment variables (see `.env.example`):

| Variable | Description | Default |
|----------|-------------|---------|
| `DATABASE_URL` | PostgreSQL connection string | `postgresql+asyncpg://hecate:hecate@localhost:5432/hecate` |
| `QDRANT_URL` | Qdrant endpoint | `http://localhost:6333` |
| `MINIO_URL` | MinIO endpoint | `localhost:9000` |
| `HECATE_API_KEYS` | Comma-separated API keys | - |
| `LLM_GUARD_ENABLED` | Enable LLM Guard scanning | `true` |
| `RATE_LIMIT_RPM` | Requests per minute per key | `60` |

## Development

```bash
# Run tests
pytest tests/ -v

# Lint
ruff check src/ tests/

# Format
ruff format src/ tests/

# Type check
mypy src/
```

## License

MIT License
