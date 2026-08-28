# Tutorial: Build Your First Agent

> **15 minutes** — Create a fully-configured agent, give it tools, and run multi-turn conversations through the OpenAI-compatible API and the `hecate` CLI.

This tutorial goes beyond the [Quickstart](../getting-started/quickstart.md). You will learn the agent data model, tool binding, session-based conversations, and both the REST API and CLI workflows.

---

## What you will learn

- How an **agent** is configured (name, persona, model, mode, tools)
- How to create, inspect, update, and delete agents via **REST API** and **CLI**
- How to bind **built-in tools** to an agent and watch it call them
- How **sessions** give conversations memory across multiple requests
- How to use the **interactive CLI chat** REPL

## Prerequisites

- Hecate running locally — complete the [Quickstart](../getting-started/quickstart.md) first
- At least one LLM provider configured in `.env` (e.g. `OPENAI_API_KEY`)
- The `hecate` CLI on your `PATH` (installed via `uv pip install -e ".[dev]"`)

> **Tip** — After finishing the Quickstart, run `hecate preflight` once to verify the database, Alembic migrations, environment variables, and LLM credentials are wired up correctly. The tutorial assumes all checks pass.

Throughout this tutorial we use `dev-key-change-me` as the API key. Replace it with whatever you set in `HECATE_API_KEYS`.

> **Using a non-OpenAI provider?** This tutorial uses `gpt-4o-mini` (OpenAI) throughout. Hecate routes all LLM traffic through [LiteLLM](https://github.com/BerriAI/litellm), so **many providers** work by changing the `model` string. Non-OpenAI providers need a LiteLLM prefix — e.g. Anthropic uses `anthropic/claude-3-5-sonnet-20241022`, DeepSeek uses `deepseek/deepseek-chat`, GLM (Zhipu) uses `zai/glm-4.7-flash`, Ollama uses `ollama/llama3.1`. Replace every `gpt-4o-mini` in this tutorial (in `model_config.model` and in chat `model` fields) with your provider's model string, and set the matching env var in `.env`. See [Configure LLM Providers](../how-to/configure-llm-providers.md) for the full prefix table and the DB-backed provider registry.

---

## Step 1 — Understand the agent model

An agent is a configuration record that tells Hecate *how* to behave when it receives a chat request. Every chat request that references an agent loads its configuration at runtime.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `name` | string | Yes | Human-readable identifier (1–255 chars) |
| `persona` | string | No | System prompt — defines the agent's character and instructions |
| `model_config` | object | Yes | LLM settings: `{"model": "gpt-4o-mini", "temperature": 0.7}` |
| `mode` | string | No (defaults to `chat`) | Execution mode: `chat`, `three_layer`, or `workflow` |
| `tools` | list | No | Tool names to bind (e.g. `["web_search"]`) |
| `skills` | list | No | Skill names to attach |
| `knowledge_base_ids` | list | No | Knowledge base UUIDs for RAG |
| `risk_level` | string | No | Risk classification for the guard layer; defaults to `LOW`. Common values are `LOW`, `MEDIUM`, `HIGH` — the schema accepts any string (no enum enforced). |

### Execution modes

| Mode | When to use |
|------|-------------|
| **`chat`** | A single LLM with tools. The default — start here. |
| **`three_layer`** | Guard → Planner → Sub-Agent pipeline for complex tasks. |
| **`workflow`** | A custom directed graph (see [Multi-Agent Orchestration](04-multi-agent.md)). |

This tutorial uses `chat` mode throughout. The other modes build on the same agent model.

---

## Step 2 — Create an agent (REST API)

Create a tech-support agent with a clear persona and a specific model:

```bash
curl -X POST http://localhost:8000/api/agents \
  -H "Authorization: Bearer dev-key-change-me" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Tech Support Agent",
    "persona": "You are a patient, precise technical support engineer. You explain concepts step by step, use concrete examples, and ask clarifying questions before diving into solutions.",
    "model_config": {"model": "gpt-4o-mini", "temperature": 0.3},
    "mode": "chat",
    "risk_level": "LOW"
  }'
```

The response is the full agent object. Copy the `id` field — you'll use it in the next steps:

```json
{
  "id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "workspace_id": "00000000-0000-0000-0000-000000000000",
  "name": "Tech Support Agent",
  "persona": "You are a patient, precise technical support engineer...",
  "model_config": {"model": "gpt-4o-mini", "temperature": 0.3},
  "mode": "chat",
  "workflow_id": null,
  "tools": [],
  "skills": [],
  "knowledge_base_ids": [],
  "risk_level": "LOW",
  "opening_remarks": null,
  "enable_suggestions": true,
  "guardrail_config": null,
  "created_at": "2026-01-15T10:30:00Z",
  "updated_at": "2026-01-15T10:30:00Z",
  "deleted": false,
  "deleted_at": null,
  "model_available": true
}
```

> **New agent fields**: `workspace_id` is the tenant the agent belongs to (defaults to your workspace); `workflow_id` is set only for `workflow` mode agents; `opening_remarks` is an optional greeting the agent sends on first message; `guardrail_config` holds per-agent guardrail overrides; `model_available` is computed at read time from the live provider registry — `true` only when both the model entry is enabled and its upstream provider is active. You can ignore most of these until later tutorials.

> **`temperature: 0.3`** — tech-support answers should be consistent and factual, so we use a low temperature. For creative tasks like brainstorming, use 0.7 or higher.

### What just happened?

Hecate stored the agent configuration in PostgreSQL. The agent is now addressable by its `id`. No LLM calls were made yet — agent creation is pure configuration.

---

## Step 3 — Create an agent (CLI)

### CLI authentication

Before using any `hecate` CLI command, configure the API key once so the CLI can authenticate against the server:

```bash
hecate config set api_key dev-key-change-me
```

This must match a key listed in `HECATE_API_KEYS` on the server (the default `dev-key-change-me` works out of the box if you copied `.env.example`). If your server runs on a non-default host or port, also set the base URL:

```bash
hecate config set base_url http://localhost:8000
```

Verify the configuration:

```bash
hecate config show
```

Now every `hecate` command in the rest of this tutorial will authenticate automatically. See [CLI Reference](../reference/cli.md) for profile management and `hecate auth login` (JWT-based auth for multi-user deployments).

### Create an agent

The `hecate` CLI provides the same capability with a simpler interface. Create a second agent — a creative writing assistant:

```bash
hecate agent create \
  --name "Creative Writing Coach" \
  --model "gpt-4o-mini" \
  --mode chat \
  --persona "You are an encouraging creative writing coach. You help users improve their prose with specific, actionable feedback."
```

List your agents to verify both were created:

```bash
hecate agent list
```

```
┏━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━┓
┃ id                       ┃ mode   ┃ name                ┃
┡━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━┩
│ a1b2c3d4-...             │ chat   │ Tech Support Agent  │
│ b2c3d4e5-...             │ chat   │ Creative Writing…   │
└──────────────────────────┴────────┴─────────────────────┘
```

Get full details for a specific agent:

```bash
hecate agent get a1b2c3d4-e5f6-7890-abcd-ef1234567890
```

> **Pagination** — `hecate agent list` returns up to 20 agents per page. Use `--page` and `--page-size` (max 100) to walk through larger workspaces: `hecate agent list --page 2 --page-size 50`.

---

## Step 4 — Chat with your agent

Hecate exposes an OpenAI-compatible endpoint at `POST /v1/agents/{agent_id}/chat/completions`. The agent ID in the URL path is authoritative — Hecate loads its persona, tools, and knowledge bases, then forwards to the LLM configured in `model_config.model`:

```bash
curl -X POST http://localhost:8000/v1/agents/a1b2c3d4-e5f6-7890-abcd-ef1234567890/chat/completions \
  -H "Authorization: Bearer dev-key-change-me" \
  -H "Content-Type: application/json" \
  -d '{
    "messages": [
      {"role": "user", "content": "My Docker container keeps exiting with code 137. What does that mean?"}
    ]
  }'
```

The body's `model` field, if present, is accepted (so the OpenAI Python SDK can be pointed at this endpoint unchanged) but ignored — the agent ID in the URL wins.

The response follows the standard OpenAI Chat Completions format. Hecate resolved the agent, injected its `persona` as the system prompt, and forwarded the request to the LLM specified in `model_config`:

```json
{
  "id": "chatcmpl-a1b2c3d4e5f6",
  "object": "chat.completion",
  "model": "gpt-4o-mini",
  "choices": [
    {
      "index": 0,
      "message": {
        "role": "assistant",
        "content": "Exit code 137 means the container received SIGKILL..."
      },
      "finish_reason": "stop"
    }
  ],
  "usage": {"prompt_tokens": 95, "completion_tokens": 142, "total_tokens": 237}
}
```

### Streaming

To receive tokens as they are produced, set `"stream": true`. The response switches to Server-Sent Events in the OpenAI Chat Completions chunk format:

```bash
curl -N -X POST http://localhost:8000/v1/agents/a1b2c3d4-e5f6-7890-abcd-ef1234567890/chat/completions \
  -H "Authorization: Bearer dev-key-change-me" \
  -H "Content-Type: application/json" \
  -d '{
    "messages": [{"role": "user", "content": "Stream me a haiku about Docker."}],
    "stream": true
  }'
```

`-N` disables curl buffering so each event arrives as the upstream LLM emits it. Stream events are JSON objects you can pipe into a JSON parser (`jq`, `python -c '...'`) for incremental processing.

> **Direct model access** — You can also call `/v1/chat/completions` with a raw model name (e.g. `"model": "gpt-4o-mini"`) without referencing an agent. This bypasses agent configuration entirely and calls the LLM directly. Useful for quick tests.

---

## Step 5 — Add a tool and watch tool calling

Agents become useful when they can take action. Hecate seeds multiple built-in tools on startup:

| Tool | Description |
|------|-------------|
| `web_search` | Search the web for information |
| `read_file` | Read a file within the agent's workspace |
| `write_file` | Write content to a file in the workspace |
| `list_files` | List files and directories in the workspace |
| `execute_code` | Execute code in a sandboxed environment |
| `browser_navigate` / `browser_click` / `browser_type` / `browser_extract` / `browser_screenshot` / `browser_fill_form` | Drive a sandboxed headless Chromium (see [Browser Automation](../how-to/browser-automation.md)) |

> **Tool execution environment** — Browser tools require a non-local agent environment (they refuse to run when `AGENT_ENV_BACKEND=local`) and enforce per-environment domain allow-lists. The `execute_code` tool runs in a Docker-isolated sandbox. The `web_search` tool defaults to DuckDuckGo (no API key needed); only switch to Tavily/Serper by setting `SEARCH_PROVIDER` and `SEARCH_API_KEY` in `.env`.

### Bind a tool to your agent

Update the Tech Support Agent to include `web_search`:

```bash
curl -X PUT http://localhost:8000/api/agents/a1b2c3d4-e5f6-7890-abcd-ef1234567890 \
  -H "Authorization: Bearer dev-key-change-me" \
  -H "Content-Type: application/json" \
  -d '{
    "tools": ["web_search"]
  }'
```

Or with the CLI:

```bash
hecate agent update a1b2c3d4-e5f6-7890-abcd-ef1234567890 --tools web_search
```

### Ask a question that triggers a search

```bash
curl -X POST http://localhost:8000/v1/agents/a1b2c3d4-e5f6-7890-abcd-ef1234567890/chat/completions \
  -H "Authorization: Bearer dev-key-change-me" \
  -H "Content-Type: application/json" \
  -d '{
    "messages": [
      {"role": "user", "content": "What is the latest stable version of Python? Search the web if you're not sure."
    ]
  }'
```

The agent now has access to the `web_search` tool. When the LLM decides it needs external information, it emits a tool call; Hecate's Pregel runtime executes the tool, feeds the result back to the LLM, and returns the final answer. You see the synthesized response — the tool-calling loop happens inside the engine.

> **Tool calling loop** — The Pregel runtime handles the LLM ↔ tool iteration automatically: the LLM proposes a tool call → the engine executes it → the result goes back to the LLM → the LLM produces the final answer. This loop continues until the LLM stops requesting tools or `max_iterations` is reached.

---

## Step 6 — Multi-turn conversations with sessions

Without a session, every request is independent — the agent has no memory of previous messages. Pass a `session_id` to maintain conversation continuity:

```bash
# First message — establishes the session
curl -X POST http://localhost:8000/v1/agents/a1b2c3d4-e5f6-7890-abcd-ef1234567890/chat/completions \
  -H "Authorization: Bearer dev-key-change-me" \
  -H "Content-Type: application/json" \
  -d '{
    "messages": [
      {"role": "user", "content": "I have a Python Flask app that won't start. The error says 'Address already in use'."
    ],
    "session_id": "550e8400-e29b-41d4-a716-446655440000"
  }'

# Follow-up — the agent remembers the context
curl -X POST http://localhost:8000/v1/agents/a1b2c3d4-e5f6-7890-abcd-ef1234567890/chat/completions \
  -H "Authorization: Bearer dev-key-change-me" \
  -H "Content-Type: application/json" \
  -d '{
    "messages": [
      {"role": "user", "content": "I tried killing the process. How do I prevent this from happening again?"
    ],
    "session_id": "550e8400-e29b-41d4-a716-446655440000"
  }'
```

The second request references "this" and the agent understands the Flask port conflict — because the session preserved the conversation history.

You can use any UUID as a `session_id`. The Hecate engine derives the conversation state from the execution event log (with a materialized cache for speed) when a `session_id` is provided, and appends the new state after each turn.

> **Concurrent requests on the same session are serialized** — Hecate acquires a per-session lock so the second request waits for the first to finish (streaming responses carry `X-Queue-Position` and `X-Queue-Wait-Ms` headers). If the lock cannot be acquired in time, the request returns HTTP 408 with error code `QUEUE_TIMEOUT`. Use distinct `session_id` values for concurrent flows to avoid queueing.

---

## Step 7 — Interactive CLI chat

For development and testing, the interactive REPL is faster than curl:

```bash
hecate chat interactive a1b2c3d4-e5f6-7890-abcd-ef1234567890
```

```
Hecate Chat — Agent: a1b2c3d4-e5f6-7890-abcd-ef1234567890
Type your message, or /clear, /history, /exit

> What's the difference between TCP and UDP?
Agent: TCP (Transmission Control Protocol) is connection-oriented...
> Give me a real-world analogy
Agent: Think of TCP like a phone call...
> /history
You: What's the difference between TCP and UDP?
Agent: TCP (Transmission Control Protocol) is connection-oriented...
You: Give me a real-world analogy
Agent: Think of TCP like a phone call...
> /exit
Goodbye!
```

The interactive mode supports streaming (tokens appear as they arrive), slash commands (`/clear`, `/history`, `/exit`), and optional session resumption via `--session-id`.

For a one-shot message without entering the REPL:

```bash
hecate chat send a1b2c3d4-e5f6-7890-abcd-ef1234567890 "Explain OAuth2 in one sentence."
```

---

## Step 8 — Manage your agents

### List and inspect

```bash
# CLI
hecate agent list
hecate agent get a1b2c3d4-e5f6-7890-abcd-ef1234567890

# API
curl http://localhost:8000/api/agents \
  -H "Authorization: Bearer dev-key-change-me"

curl http://localhost:8000/api/agents/a1b2c3d4-e5f6-7890-abcd-ef1234567890 \
  -H "Authorization: Bearer dev-key-change-me"
```

### Update

```bash
# Change the persona
hecate agent update a1b2c3d4-e5f6-7890-abcd-ef1234567890 \
  --persona "You are a senior DevOps engineer. Be concise and practical."

# Add more tools
hecate agent update a1b2c3d4-e5f6-7890-abcd-ef1234567890 \
  --tools web_search,read_file,list_files
```

> **CLI update scope** — `hecate agent update` only accepts `--name`, `--persona`, `--tools`, and `--kb-ids`. To change `mode`, `model_config`, `enable_suggestions`, `opening_remarks`, or `guardrail_config`, use the REST API:
> 
> ```bash
> curl -X PUT http://localhost:8000/api/agents/a1b2c3d4-e5f6-7890-abcd-ef1234567890 \
>   -H "Authorization: Bearer dev-key-change-me" \
>   -H "Content-Type: application/json" \
>   -d '{"mode": "workflow", "model_config": {"model": "gpt-4o-mini", "temperature": 0.2}}'
> ```

### Bind and unbind skills

Skills attach to an agent by name. In addition to setting `skills` on create or via `PUT`, you can attach or detach a single skill with dedicated endpoints (useful when skills are added/removed dynamically):

```bash
# Attach a skill (idempotent — re-running is a no-op)
curl -X POST http://localhost:8000/api/agents/a1b2c3d4-e5f6-7890-abcd-ef1234567890/skills \
  -H "Authorization: Bearer dev-key-change-me" \
  -H "Content-Type: application/json" \
  -d '{"skill_name": "code-review"}'

# Detach a skill (idempotent)
curl -X DELETE http://localhost:8000/api/agents/a1b2c3d4-e5f6-7890-abcd-ef1234567890/skills/code-review \
  -H "Authorization: Bearer dev-key-change-me"
```

Both endpoints return `{"skills": ["..."]}` with the updated list. The skill must already be registered in the agent's workspace (or the shared zero-UUID workspace); otherwise the attach endpoint returns 404 with error code `SKILL_NOT_FOUND`. There is no CLI equivalent — use the REST endpoints above. See [Skills concept](../concepts/skills.md) for the workspace skill registry.

### Export and import

Export an agent to portable JSON (useful for backups or moving agents between environments):

```bash
curl http://localhost:8000/api/agents/a1b2c3d4-e5f6-7890-abcd-ef1234567890/export \
  -H "Authorization: Bearer dev-key-change-me" > tech-support-agent.json
```

Import it into another workspace:

```bash
curl -X POST http://localhost:8000/api/agents/import \
  -H "Authorization: Bearer dev-key-change-me" \
  -H "Content-Type: application/json" \
  -d @tech-support-agent.json
```

### Delete (soft delete)

```bash
# CLI (asks for confirmation)
hecate agent delete a1b2c3d4-e5f6-7890-abcd-ef1234567890

# API
curl -X DELETE http://localhost:8000/api/agents/a1b2c3d4-e5f6-7890-abcd-ef1234567890 \
  -H "Authorization: Bearer dev-key-change-me"
```

Deletion is soft — the record stays in the database but is hidden from list views and chat requests.

---

## How it fits together

```
┌──────────────────────────────────────────────────────────┐
│  Client (curl / hecate CLI / OpenAI SDK)                 │
└──────────────────────┬───────────────────────────────────┘
                       │  POST /v1/agents/<AGENT_ID>/chat/completions
                       │  body: { messages: [...] }
                       ▼
┌──────────────────────────────────────────────────────────┐
│  Hecate API Layer                                        │
│  ┌────────────────────────────────────────────────────┐  │
│  │  Resolve agent by ID                               │  │
│  │  Load: persona + model_config + tools + skills     │  │
│  │  Restore session state (if session_id provided)     │  │
│  └───────────────────────┬────────────────────────────┘  │
└──────────────────────────┼───────────────────────────────┘
                           ▼
┌──────────────────────────────────────────────────────────┐
│  Pregel Runtime (Superstep Loop)                         │
│                                                          │
│   ┌─────────┐     ┌──────────┐     ┌──────────────┐     │
│   │  LLM    │────▶│  Tool?   │─yes─▶│ Execute Tool │     │
│   │  Call   │     │  No →    │     │ (web_search, │     │
│   │         │◀────│  done    │◀────│  read_file…) │     │
│   └─────────┘     └──────────┘     └──────────────┘     │
│                                                          │
│   Each iteration = one superstep                         │
│   Event log commits after each step                      │
└──────────────────────────────┬───────────────────────────┘
                               ▼
┌──────────────────────────────────────────────────────────┐
│  OpenAI-compatible response                              │
│  + Session state persisted                               │
└──────────────────────────────────────────────────────────┘
```

---

## Troubleshooting

### Chat returns 404 for `/v1/agents/{id}/chat/completions`

The agent ID is wrong, or the agent was deleted. Run `hecate agent list` to verify the ID. Agent IDs are UUIDs — make sure there are no typos or truncation.

### Chat returns 400 `DEPRECATED_ROUTING` 

You're using the legacy `model: "agent/<UUID>"` form on `POST /v1/chat/completions`. Replace with `POST /v1/agents/<UUID>/chat/completions` (agent ID in the URL path, no `model` field needed). The response body includes the new endpoint under `error.details.new_endpoint`.

### Tool calls never happen

The agent's `tools` list is empty. Verify with `hecate agent get <id>` — the `tools` array must contain the tool name (e.g. `["web_search"]`). Also check that the model you chose supports function calling (most modern models do; very small or legacy models may not).

### `web_search` returns errors

The `web_search` tool requires a search provider backend. Set `SEARCH_PROVIDER` and the corresponding API key (e.g. `SEARCH_API_KEY`) in `.env`. See [Environment Variables](../reference/env-vars.md) for details.

### Agent responses don't reflect the persona

Make sure you're calling `POST /v1/agents/<AGENT_ID>/chat/completions` (agent ID in the URL path) — not `POST /v1/chat/completions` with a bare model name. The bare model name (e.g. `"gpt-4o-mini"`) bypasses agent configuration entirely and calls the LLM directly without persona, tools, or knowledge bases.

---

## Summary

You now know how to:

- **Create agents** with persona, model, and mode configuration
- **Chat with agents** via the OpenAI-compatible API and the CLI
- **Bind tools** and let the Pregel runtime handle the tool-calling loop
- **Use sessions** for multi-turn conversations with memory
- **Manage agents** — list, update, export, import, and delete

## Next steps

- **[Knowledge Base and RAG](02-knowledge-base.md)** — Upload documents and let your agent answer from your own data.
- **[MCP Tool Integration](03-mcp-integration.md)** — Connect external MCP servers or expose Hecate as an MCP server.
- **[Multi-Agent Orchestration](04-multi-agent.md)** — Build workflows with handoff, pipeline, and broadcast patterns.
- **[Architecture Overview](../concepts/overview.md)** — Understand the five-layer architecture and the Pregel runtime.
- **[CLI Reference](../reference/cli.md)** — All `hecate` commands and flags.
