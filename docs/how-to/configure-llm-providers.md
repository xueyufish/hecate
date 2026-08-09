# How to Configure LLM Providers

> Wire up OpenAI, Anthropic, DeepSeek, Qwen, GLM, Ollama, or any LiteLLM-supported provider — via environment variables or the database-backed provider registry.

Hecate routes all LLM traffic through [LiteLLM](https://github.com/BerriAI/litellm), which means **100+ providers** work out of the box — including OpenAI-compatible endpoints, Anthropic, Chinese cloud APIs, and local models. There are two configuration paths, and you can use either or both.

---

## Two ways to configure providers

| Path | Where stored | Best for |
|------|--------------|----------|
| **Environment variables** | `.env` file | Quick setup, single-provider deployments, Docker Compose |
| **Database-backed providers** | PostgreSQL (encrypted) | Multi-tenant environments, runtime management, enable/disable without restart |

### How they interact

- Env-var keys work for raw model-name chat calls (e.g. `"model": "gpt-4o-mini"`).
- DB-registered providers feed the model registry; agents that reference a registered model get availability checks and provider-level config (timeout, retries).
- Both paths are valid simultaneously — env vars are the LiteLLM fallback for any model not in the registry.

---

## Option 1 — Environment variables (quickest)

Edit `.env` and set the provider key. Restart the server.

```dotenv
# .env

# At least one of these:
OPENAI_API_KEY=sk-...
ANTHROPIC_API_KEY=sk-ant-...
DEEPSEEK_API_KEY=sk-...
DASHSCOPE_API_KEY=sk-...         # Alibaba Qwen / Tongyi
ZHIPU_API_KEY=...                # Zhipu GLM / ChatGLM
GEMINI_API_KEY=...               # Google Gemini
GROQ_API_KEY=gsk_...
TOGETHERAI_API_KEY=...
OPENROUTER_API_KEY=sk-or-...
```

Any environment variable LiteLLM recognizes will work — see the [full list of supported providers](https://docs.litellm.ai/docs/providers).

### Using a provider

Reference the provider using LiteLLM's prefix convention in the `model` field:

```bash
curl -X POST http://localhost:8000/v1/chat/completions \
  -H "Authorization: Bearer dev-key-change-me" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "deepseek/deepseek-chat",
    "messages": [{"role": "user", "content": "Hello!"}]
  }'
```

### Provider prefix table

| Provider | Env var | Model string | Notes |
|----------|---------|--------------|-------|
| OpenAI | `OPENAI_API_KEY` | `gpt-4o`, `gpt-4o-mini`, `o1-mini` | No prefix needed |
| Anthropic | `ANTHROPIC_API_KEY` | `claude-3-5-sonnet-20241022`, `claude-3-opus-20240229` | No prefix needed |
| DeepSeek | `DEEPSEEK_API_KEY` | `deepseek/deepseek-chat`, `deepseek/deepseek-reasoner` | DeepSeek-V3, DeepSeek-R1 |
| Qwen (DashScope) | `DASHSCOPE_API_KEY` | `qwen-turbo`, `qwen-plus`, `qwen-max` | Alibaba Tongyi Qianwen |
| GLM (Zhipu) | `ZHIPU_API_KEY` | `zhipu/glm-4`, `zhipu/glm-4-flash` | ChatGLM series |
| Google Gemini | `GEMINI_API_KEY` | `gemini/gemini-1.5-pro`, `gemini/gemini-2.0-flash` | |
| Groq | `GROQ_API_KEY` | `groq/llama-3.1-70b-versatile` | Fast inference |
| Ollama (local) | — | `ollama/llama3.1`, `ollama/qwen2.5` | No API key; see below |

### Local models with Ollama

Ollama serves any open-source model on `localhost:11434` — no API key required.

```bash
# Start Ollama (separate terminal)
ollama serve

# Pull a model
ollama pull qwen2.5
# or: ollama pull llama3.1, deepseek-r1, mistral, etc.
```

Hecate detects the `ollama/` prefix and routes to the local endpoint. No `.env` changes needed.

```bash
curl -X POST http://localhost:8000/v1/chat/completions \
  -H "Authorization: Bearer dev-key-change-me" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "ollama/llama3.1",
    "messages": [{"role": "user", "content": "Hello!"}]
  }'
```

---

## Option 2 — Database-backed provider registry

For multi-tenant deployments or when you need to manage providers at runtime without restarting Hecate.

### What the registry gives you

- **Encrypted API key storage** (Fernet symmetric encryption)
- **Model auto-discovery** — Hecate queries the provider's `/models` endpoint at registration
- **Status tracking** — providers show `active`, `error`, or `inactive` based on connectivity tests
- **Per-provider config** — timeout, max retries, rate limit override
- **Enable/disable** — toggle a provider without deleting its configuration
- **Multi-tenant isolation** — providers are workspace-scoped

### Encrypt API keys first

The API key you store in the DB is encrypted at rest. Generate a Fernet key and add it to `.env`:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

```dotenv
# .env
FERNET_KEY=your-generated-fernet-key
```

> **Without `FERNET_KEY`** the system falls back to plaintext storage — fine for local development, **never** for production.

### Create a provider via API

```bash
curl -X POST http://localhost:8000/api/model-providers \
  -H "Authorization: Bearer dev-key-change-me" \
  -H "Content-Type: application/json" \
  -d '{
    "display_name": "OpenAI Production",
    "api_key": "sk-...",
    "is_enabled": true
  }'
```

Hecate creates the provider, encrypts the API key, and queries OpenAI's `/models` endpoint to populate the registry. The response includes the model count:

```json
{
  "id": "uuid",
  "name": "openai-production",
  "display_name": "OpenAI Production",
  "base_url": null,
  "is_enabled": true,
  "status": "pending",
  "model_count": 47
}
```

### Create a provider via CLI

```bash
hecate model providers create \
  --name "Anthropic Prod" \
  --type "anthropic" \
  --api-key "sk-ant-..."
```

### Test connectivity

```bash
# API
curl -X POST http://localhost:8000/api/model-providers/{PROVIDER_ID}/test \
  -H "Authorization: Bearer dev-key-change-me"

# CLI
hecate model providers test {PROVIDER_ID}
```

The test hits the provider's `/models` endpoint, checks authentication, and reports response latency:

```json
{
  "status": "active",
  "response_time_ms": 234
}
```

### List providers and registered models

```bash
# API
curl http://localhost:8000/api/model-providers \
  -H "Authorization: Bearer dev-key-change-me"

curl http://localhost:8000/api/models \
  -H "Authorization: Bearer dev-key-change-me"

# CLI
hecate model providers list
hecate model list
```

### Use a registered model in an agent

When a model is in the registry, agents get availability tracking. Create an agent referencing one of the registered models:

```bash
curl -X POST http://localhost:8000/api/agents \
  -H "Authorization: Bearer dev-key-change-me" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "GPT-4o Agent",
    "model_config": {"model": "gpt-4o"},
    "mode": "chat"
  }'
```

The agent creation flow checks the registry. If the model is disabled or the provider is inactive, the API returns an error — preventing silent failures at chat time.

### Disable a provider

```bash
# API
curl -X PUT http://localhost:8000/api/model-providers/{PROVIDER_ID} \
  -H "Authorization: Bearer dev-key-change-me" \
  -H "Content-Type: application/json" \
  -d '{"is_enabled": false}'

# CLI
hecate model providers delete {PROVIDER_ID} --force
```

Disabling a provider cascades to all its registered models. Agents referencing those models will reject chat requests until re-enabled.

### Add a custom model

If a model exists at the provider but wasn't auto-discovered, add it manually:

```bash
curl -X POST http://localhost:8000/api/models \
  -H "Authorization: Bearer dev-key-change-me" \
  -H "Content-Type: application/json" \
  -d '{
    "provider_id": "uuid-of-provider",
    "model_id": "gpt-4o-2024-08-06",
    "display_name": "GPT-4o (August 2024)"
  }'
```

### Provider-level config overrides

Each provider stores `timeout`, `max_retries`, and `rate_limit_rpm` in its `config` JSON. Override these when creating or updating:

```bash
curl -X PUT http://localhost:8000/api/model-providers/{PROVIDER_ID} \
  -H "Authorization: Bearer dev-key-change-me" \
  -H "Content-Type: application/json" \
  -d '{
    "config": {
      "timeout": 60,
      "max_retries": 5,
      "rate_limit_rpm": 100
    }
  }'
```

| Field | Default | Range | Effect |
|-------|---------|-------|--------|
| `timeout` | 30s | 1–300 | LiteLLM request timeout |
| `max_retries` | 3 | 0–10 | Retry count on transient errors |
| `rate_limit_rpm` | 60 | 1–10000 | Per-provider requests per minute cap |

---

## Web search provider (`web_search` built-in tool)

The `web_search` tool (available on every agent) uses a configurable search backend. The default is DuckDuckGo — no API key needed.

### Configure in `.env`

```dotenv
# .env

# Options: "duckduckgo" (default), "tavily", "serper"
SEARCH_PROVIDER=duckduckgo

# Required only for tavily / serper
SEARCH_API_KEY=
```

### Provider comparison

| Provider | API key | Cost | Best for |
|----------|---------|------|----------|
| **DuckDuckGo** | Not needed | Free | Development, low-volume use |
| **Tavily** | Required | Paid (free tier) | Production RAG, clean structured results |
| **Serper** | Required | Paid (Google results) | Production RAG, Google SERP data |

### Example: switch to Tavily

```dotenv
# .env
SEARCH_PROVIDER=tavily
SEARCH_API_KEY=tvly-...
```

Restart the server. Agents using `web_search` will now query Tavily.

---

## Troubleshooting

### Chat request returns 401/403 from the LLM provider

The API key for the model you requested is missing or invalid. Verify:
- **Env var path**: the variable is set in `.env` and you restarted `uvicorn`.
- **DB provider path**: run `hecate model providers test <id>` — it hits the `/models` endpoint and confirms authentication.

### Provider shows `error` status after test

The test endpoint hits `{base_url}/models` and checks the HTTP status:
- `200` → `active`
- `401`/`403` → `error: Authentication failed — API key rejected`
- Connection timeout (15s) → `error: Connection timed out`

Double-check the API key and that outbound HTTPS to the provider is allowed.

### `litellm` not installed

LLM providers require the `litellm` package. Install the optional group:

```bash
uv pip install -e ".[llm]"
```

### Model not in registry but env var is set

Env-var-only providers don't appear in `GET /api/models`. This is expected — the registry tracks DB-registered providers. For availability tracking and multi-tenant isolation, register the provider via `POST /api/model-providers`.

### `web_search` returns empty results

- **DuckDuckGo**: usually works without config, but rate-limited. Switch to Tavily or Serper for production.
- **Tavily/Serper**: `SEARCH_API_KEY` is missing or invalid. Check the env var.
- **All providers**: outbound HTTPS may be blocked. Test with `curl https://api.tavily.com/` from the server.

### Custom provider base URL (OpenAI-compatible APIs)

If you run an OpenAI-compatible endpoint (vLLM, llama.cpp server, LocalAI, etc.):

```bash
curl -X POST http://localhost:8000/api/model-providers \
  -H "Authorization: Bearer dev-key-change-me" \
  -H "Content-Type: application/json" \
  -d '{
    "display_name": "Local vLLM",
    "api_key": "not-required",
    "base_url": "http://localhost:8001/v1"
  }'
```

Hecate prepends `openai/` to model names when a `base_url` is set, routing through LiteLLM's OpenAI-compatible adapter.

---

## Further reading

- **[Environment Variables Reference](../reference/env-vars.md)** — every Hecate env var, with defaults.
- **[CLI Reference](../reference/cli.md)** — full `hecate model providers` command tree.
- **[LiteLLM Providers](https://docs.litellm.ai/docs/providers)** — the authoritative list of 100+ supported providers and their env var conventions.
- **[Tutorial: Build Your First Agent](../tutorials/01-first-agent.md)** — see model configuration in a working agent.