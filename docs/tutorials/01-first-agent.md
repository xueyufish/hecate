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

Throughout this tutorial we use `dev-key-change-me` as the API key. Replace it with whatever you set in `HECATE_API_KEYS`.

---

## Step 1 — Understand the agent model

An agent is a configuration record that tells Hecate *how* to behave when it receives a chat request. Every chat request that references an agent loads its configuration at runtime.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `name` | string | Yes | Human-readable identifier (1–255 chars) |
| `persona` | string | No | System prompt — defines the agent's character and instructions |
| `model_config` | object | Yes | LLM settings: `{"model": "gpt-4o-mini", "temperature": 0.7}` |
| `mode` | string | No | Execution mode: `chat` (default), `three_layer`, or `workflow` |
| `tools` | list | No | Tool names to bind (e.g. `["web_search"]`) |
| `skills` | list | No | Skill names to attach |
| `knowledge_base_ids` | list | No | Knowledge base UUIDs for RAG |
| `risk_level` | string | No | Risk classification for the guard layer: `LOW` (default), `MEDIUM`, `HIGH` |

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
  "name": "Tech Support Agent",
  "persona": "You are a patient, precise technical support engineer...",
  "model_config": {"model": "gpt-4o-mini", "temperature": 0.3},
  "mode": "chat",
  "tools": [],
  "skills": [],
  "knowledge_base_ids": [],
  "risk_level": "LOW",
  "enable_suggestions": true,
  "created_at": "2026-01-15T10:30:00Z",
  "updated_at": "2026-01-15T10:30:00Z",
  "deleted": false,
  "deleted_at": null
}
```

> **`temperature: 0.3`** — tech-support answers should be consistent and factual, so we use a low temperature. For creative tasks like brainstorming, use 0.7 or higher.

### What just happened?

Hecate stored the agent configuration in PostgreSQL. The agent is now addressable by its `id`. No LLM calls were made yet — agent creation is pure configuration.

---

## Step 3 — Create an agent (CLI)

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

---

## Step 4 — Chat with your agent

Hecate exposes an OpenAI-compatible endpoint at `POST /v1/chat/completions`. To address a specific agent, use `agent/<AGENT_ID>` as the `model` field:

```bash
curl -X POST http://localhost:8000/v1/chat/completions \
  -H "Authorization: Bearer dev-key-change-me" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "agent/a1b2c3d4-e5f6-7890-abcd-ef1234567890",
    "messages": [
      {"role": "user", "content": "My Docker container keeps exiting with code 137. What does that mean?"}
    ]
  }'
```

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

> **Direct model access** — You can also call `/v1/chat/completions` with a raw model name (e.g. `"model": "gpt-4o-mini"`) without referencing an agent. This bypasses agent configuration entirely and calls the LLM directly. Useful for quick tests.

---

## Step 5 — Add a tool and watch tool calling

Agents become useful when they can take action. Hecate seeds five built-in tools on startup:

| Tool | Description |
|------|-------------|
| `web_search` | Search the web for information |
| `read_file` | Read a file within the agent's workspace |
| `write_file` | Write content to a file in the workspace |
| `list_files` | List files and directories in the workspace |
| `execute_code` | Execute code in a sandboxed environment |

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
curl -X POST http://localhost:8000/v1/chat/completions \
  -H "Authorization: Bearer dev-key-change-me" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "agent/a1b2c3d4-e5f6-7890-abcd-ef1234567890",
    "messages": [
      {"role": "user", "content": "What is the latest stable version of Python? Search the web if you're not sure."}
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
curl -X POST http://localhost:8000/v1/chat/completions \
  -H "Authorization: Bearer dev-key-change-me" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "agent/a1b2c3d4-e5f6-7890-abcd-ef1234567890",
    "messages": [
      {"role": "user", "content": "I have a Python Flask app that won't start. The error says 'Address already in use'."}
    ],
    "session_id": "550e8400-e29b-41d4-a716-446655440000"
  }'

# Follow-up — the agent remembers the context
curl -X POST http://localhost:8000/v1/chat/completions \
  -H "Authorization: Bearer dev-key-change-me" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "agent/a1b2c3d4-e5f6-7890-abcd-ef1234567890",
    "messages": [
      {"role": "user", "content": "I tried killing the process. How do I prevent this from happening again?"}
    ],
    "session_id": "550e8400-e29b-41d4-a716-446655440000"
  }'
```

The second request references "this" and the agent understands the Flask port conflict — because the session preserved the conversation history.

You can use any UUID as a `session_id`. The Hecate engine loads the conversation state from the checkpoint store when a `session_id` is provided, and persists the new state after each turn.

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
                       │  POST /v1/chat/completions
                       │  model: "agent/<AGENT_ID>"
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
│   Checkpoints saved after each step                      │
└──────────────────────────────┬───────────────────────────┘
                               ▼
┌──────────────────────────────────────────────────────────┐
│  OpenAI-compatible response                              │
│  + Session state persisted                               │
└──────────────────────────────────────────────────────────┘
```

---

## Troubleshooting

### Chat returns 404 for `agent/<AGENT_ID>`

The agent ID is wrong, or the agent was deleted. Run `hecate agent list` to verify the ID. Agent IDs are UUIDs — make sure there are no typos or truncation.

### Tool calls never happen

The agent's `tools` list is empty. Verify with `hecate agent get <id>` — the `tools` array must contain the tool name (e.g. `["web_search"]`). Also check that the model you chose supports function calling (most modern models do; very small or legacy models may not).

### `web_search` returns errors

The `web_search` tool requires a search provider backend. Set `SEARCH_PROVIDER` and the corresponding API key (e.g. `SEARCH_API_KEY`) in `.env`. See [Environment Variables](../reference/env-vars.md) for details.

### Agent responses don't reflect the persona

Make sure you're using `model: "agent/<AGENT_ID>"` — not a bare model name. A bare model name like `"gpt-4o-mini"` bypasses agent configuration entirely and calls the LLM directly.

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
