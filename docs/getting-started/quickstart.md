# Quickstart

Get Hecate running locally in ~5 minutes and send your first chat request.

This guide starts Hecate on `http://localhost:8000` with PostgreSQL, Qdrant, and MinIO via Docker Compose. You will install the Python app, run migrations, start the server, and verify everything works with a `curl` request.

---

## Prerequisites

| Tool | Version | Why |
|------|---------|-----|
| Python | 3.12+ | Hecate runtime |
| `uv` | latest | Fast Python package manager |
| Docker + Docker Compose | latest | Runs PostgreSQL, Qdrant, MinIO, Temporal |
| An LLM API key | OpenAI, Anthropic, DeepSeek, Qwen, GLM, Ollama, or any [LiteLLM-supported provider](https://docs.litellm.ai/docs/providers) | Required for chat completions |

Verify Python and Docker are installed:

```bash
python3 --version    # must be 3.12+
docker --version
docker compose version
```

Install `uv` (one-time):

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

---

## Step 1 — Clone and install

```bash
# Clone the repository and enter the project directory
git clone https://github.com/xueyufish/hecate.git
cd hecate

# Create the virtual environment only if missing (keeps re-runs non-interactive)
[ -d .venv ] || uv venv
# Activate the virtual environment
source .venv/bin/activate
# Install Hecate in editable mode with dev dependencies
# (--prerelease=allow: required while fastmcp 4.x is only available as a beta)
uv pip install --prerelease=allow -e ".[dev]"
```

This installs Hecate in editable mode with the dev dependencies (pytest, ruff, mypy). `--prerelease=allow` is needed while the pinned `fastmcp` is only available as a beta release — without it, uv refuses to resolve transitive pre-release dependencies. After this step, two console scripts are on your `PATH`:

- `hecate` — the main CLI (agent / session / KB / tool management)
- `hecate-migrate` — the standalone migration runner

---

## Step 2 — Start infrastructure

From the repo root, copy the environment template first — Docker Compose requires the `.env` file to exist (you will fill in the API keys in [Step 3](#step-3--configure-environment)):

```bash
cp .env.example .env

docker compose -f docker/docker-compose.yml up -d postgres qdrant minio temporal
```

This starts the four infrastructure services with healthchecks:

| Service | Port | Purpose |
|---------|------|---------|
| PostgreSQL 16 | 5432 | Primary database |
| Qdrant | 6333 (HTTP), 6334 (gRPC) | Vector store for RAG |
| MinIO | 9000 (S3 API), 9001 (console) | Object storage for documents |
| Temporal | 7233 | Optional — durable workflow execution |

Wait for the services to become healthy:

```bash
docker compose -f docker/docker-compose.yml ps
```

All four should show `(healthy)` in the status column.

---

## Step 3 — Configure environment

You copied the template in Step 2. Open `.env` and fill in two values:

Open `.env` and set:

```dotenv
# At minimum, set a Hecate API key (any string you choose — clients will send this)
HECATE_API_KEYS=dev-key-change-me

# Set at least one LLM provider key
OPENAI_API_KEY=sk-...your-key...
# or
ANTHROPIC_API_KEY=sk-ant-...your-key...
```

The defaults for `DATABASE_URL`, `QDRANT_URL`, `MINIO_URL`, and `POSTGRES_PASSWORD` already match the Docker Compose services — leave them unchanged.

### Using other LLM providers

Hecate routes all LLM traffic through [LiteLLM](https://github.com/BerriAI/litellm), so you can use 100+ providers — including open-source models hosted on cloud APIs or running locally. Set the provider's API key in `.env`, then use the corresponding model prefix in your API requests.

| Provider | Env var in `.env` | Model string in request | Notes |
|----------|-------------------|------------------------|-------|
| DeepSeek | `DEEPSEEK_API_KEY=sk-...` | `deepseek/deepseek-chat` | DeepSeek-V3, DeepSeek-R1 |
| Qwen (Alibaba) | `DASHSCOPE_API_KEY=sk-...` | `dashscope/qwen-turbo`, `dashscope/qwen-plus`, `dashscope/qwen-max` | Tongyi Qianwen via DashScope |
| GLM (Zhipu) | `ZAI_API_KEY=...` | `zai/glm-4.7-flash`, `zai/glm-4-flash` | ChatGLM series |
| Ollama (local) | — | `ollama/llama3.1`, `ollama/qwen2.5` | No API key; requires `ollama serve` running on `localhost:11434` |

For example, to use DeepSeek:

```dotenv
# .env
HECATE_API_KEYS=dev-key-change-me
DEEPSEEK_API_KEY=sk-your-deepseek-key
```

Then in Step 6, pass the LiteLLM model prefix:

```bash
curl -X POST http://localhost:8000/v1/chat/completions \
  -H "Authorization: Bearer dev-key-change-me" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "deepseek/deepseek-chat",
    "messages": [{"role": "user", "content": "Hello!"}]
  }'
```

For local models via Ollama, first start Ollama and pull a model:

```bash
ollama serve          # in a separate terminal
ollama pull qwen2.5   # or llama3.1, deepseek-r1, etc.
```

No API key is needed — Hecate detects the `ollama/` prefix and routes to `http://localhost:11434`.

For providers not listed here, check the [LiteLLM provider documentation](https://docs.litellm.ai/docs/providers) for the correct env var and model prefix.

---

## Step 4 — Run database migrations

```bash
alembic upgrade head
```

Or, equivalently, using the standalone migration runner (the same command Docker Compose uses internally):

```bash
hecate-migrate
```

You should see a sequence of `Running upgrade ... -> ...` lines ending at the current head.

---

## Step 5 — Start the Hecate server

```bash
uvicorn hecate.main:app --reload
```

The first boot takes a few seconds while the lifespan handler seeds built-in tools, registers secret providers, and pre-warms optional subsystems. When you see:

```
INFO:     Uvicorn running on http://127.0.0.1:8000 (Press CTRL+C to quit)
INFO:     Application startup complete.
```

…you are ready.

Open `http://localhost:8000/docs` in a browser for the interactive Swagger UI, or `http://localhost:8000/redoc` for the ReDoc view.

---

## Step 6 — Send your first chat request

Hecate exposes an OpenAI-compatible `/v1/chat/completions` endpoint. With the API key you set in `.env`, send a one-shot chat:

```bash
curl -X POST http://localhost:8000/v1/chat/completions \
  -H "Authorization: Bearer dev-key-change-me" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gpt-4o-mini",
    "messages": [
      {"role": "user", "content": "Say hello in one sentence."}
    ]
  }'
```

You should receive a JSON response shaped exactly like the OpenAI Chat Completions response — Hecate is a drop-in replacement at this endpoint.

If you set `ANTHROPIC_API_KEY` instead, use a Claude model name with the `anthropic/` prefix in the `model` field (`anthropic/claude-3-5-sonnet-20241022`, etc.). Hecate routes the request to the right provider via LiteLLM. For other providers (DeepSeek, Qwen, GLM, Ollama, etc.), see [Using other LLM providers](#using-other-llm-providers) above.

---

## Step 7 — Create your first agent

The `/api/agents` endpoint creates a managed agent. The simplest possible agent is a chat-mode agent with a persona and a model:

```bash
curl -X POST http://localhost:8000/api/agents \
  -H "Authorization: Bearer dev-key-change-me" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Quickstart Agent",
    "persona": "You are a concise, friendly assistant.",
    "model_config": {"model": "gpt-4o-mini"},
    "mode": "chat"
  }'
```

The response includes the new agent's `id` (a UUID). Copy it for the next step.

---

## Step 8 — Chat with your agent

Send a message to the agent you just created by passing its `id` as the `model` field. Hecate resolves the agent, loads its persona, tools, and knowledge bases, and runs the conversation through the Pregel runtime:

```bash
curl -X POST http://localhost:8000/v1/chat/completions \
  -H "Authorization: Bearer dev-key-change-me" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "agent/<AGENT_ID>",
    "messages": [
      {"role": "user", "content": "What can you help me with?"}
    ]
  }'
```

Replace `<AGENT_ID>` with the UUID from Step 7. The response is the same OpenAI-compatible shape, but now produced by your configured agent — with its persona, tools, and any knowledge bases you attach.

---

## Step 9 — Stop and clean up

Stop the Hecate server with `Ctrl+C` in the `uvicorn` terminal.

Stop the infrastructure:

```bash
docker compose -f docker/docker-compose.yml down
```

Add `-v` to also delete the data volumes (PostgreSQL, Qdrant, MinIO) if you want a fully clean slate next time:

```bash
docker compose -f docker/docker-compose.yml down -v
```

---

## Troubleshooting

### 1. `alembic upgrade head` fails with connection refused

The PostgreSQL container is not ready yet. Re-run `docker compose -f docker/docker-compose.yml ps` and wait until `postgres` shows `(healthy)`. On slower machines this can take 30+ seconds after the first `up`.

### 2. Chat request returns 401

The `Authorization: Bearer <key>` header must match one of the keys in `HECATE_API_KEYS`. Check that you used the same string in `.env` and in the `curl` command.

### 3. Chat request returns 500 with an LLM provider error

The API key for the model you requested is missing or invalid. Open `.env`, verify the relevant provider key (`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `DEEPSEEK_API_KEY`, `DASHSCOPE_API_KEY`, `ZAI_API_KEY`, etc.), and restart `uvicorn`.

### 4. Port 5432 / 6333 / 9000 already in use

Another local service is bound to that port. Either stop the conflicting service or remap the port in `docker/docker-compose.yml`.

### 5. `hecate-migrate` command not found

Activate the virtual environment first: `source .venv/bin/activate`. The console scripts are installed into `.venv/bin/`, which must be on your `PATH`.

---

## Next steps

- **[Build your first agent](../tutorials/01-first-agent.md)** — go deeper: tool binding, sessions, CLI workflows, and agent management.
- **[Create a knowledge base](../tutorials/02-knowledge-base.md)** — upload documents and let your agent answer from them via RAG.
- **[Connect MCP tools](../tutorials/03-mcp-integration.md)** — wire up external tool servers (or expose Hecate itself as an MCP server).
- **[Build a multi-agent workflow](../tutorials/04-multi-agent.md)** — orchestrate several agents with handoff, pipeline, or broadcast patterns.
- **[Architecture overview](../concepts/overview.md)** — understand the five-layer architecture and the Pregel runtime before customising anything.
- **[Configuration reference](../reference/env-vars.md)** — every environment variable, with defaults and notes.
