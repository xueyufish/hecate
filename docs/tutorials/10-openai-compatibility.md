# Tutorial: OpenAI SDK Compatibility

> **20 minutes** — Use Hecate as a drop-in replacement for OpenAI. Point any OpenAI-compatible client (`openai-python`, `litellm`, `langchain-openai`, `instructor`, `vllm`, `llama-index`) at Hecate and keep your existing code.

Hecate's `/v1/chat/completions` and `/v1/models` endpoints are wire-compatible with OpenAI. This means any code that currently talks to OpenAI can talk to Hecate with one line of change — typically `base_url`. Useful for:

- **Local-first development** — run Hecate against your own LLM credits without rewriting client code
- **Vendor flexibility** — swap between OpenAI, Anthropic, DeepSeek, Qwen, Ollama, etc. via Hecate's model routing
- **On-prem OpenAI alternative** — keep your prompts inside your network for compliance

This tutorial covers the five common OpenAI-API patterns: chat, streaming, function calling, structured outputs, and embeddings.

---

## What you will learn

- How to point the **official `openai` SDK** at Hecate
- How to handle **multi-turn chat** with conversation history
- How to use **streaming** (`stream=True`) and consume SSE
- How **function calling / tool use** works through Hecate's MCP and custom tools
- How to request **structured outputs** (`response_format`)
- How Hecate's model routing extends OpenAI's `model` field with `agent/<id>` and `provider/<id>`
- What Hecate exposes at `/v1/models` and what it does not (no `/v1/embeddings` — embeddings are KB-internal)

## Prerequisites

- Hecate running locally — see [Quickstart](../getting-started/quickstart.md)
- An LLM provider configured in `.env` (e.g. `OPENAI_API_KEY=...`)
- At least one agent created (see [Build Your First Agent](01-first-agent.md)) — only needed for the agent-specific examples
- `openai` Python package: `uv pip install openai`

Throughout this tutorial we use `dev-key-change-me` as the Hecate API key (whatever you set in `HECATE_API_KEYS`).

---

## Step 1 — Switch your `openai` client to Hecate

The change is one line: `base_url`.

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://localhost:8000/v1",   # was: https://api.openai.com/v1
    api_key="dev-key-change-me",            # was: your OpenAI key
)

resp = client.chat.completions.create(
    model="gpt-4o-mini",
    messages=[{"role": "user", "content": "Hello!"}],
)
print(resp.choices[0].message.content)
```

That's it. Every call below uses this same `client` object.

> **Reading the API key from `.env`** — in real code, don't hardcode. The Quickstart README has a one-liner that pulls `OPENAI_API_KEY=` from `.env`. If your OpenAI SDK base URL points at Hecate, you can use either the Hecate API key (`HECATE_API_KEYS=...`) or your upstream LLM provider key — both work because Hecate proxies to the configured LLM.

---

## Step 2 — Multi-turn chat

`messages` is just a list. Pass the full history each turn:

```python
history = [
    {"role": "system", "content": "You are a concise technical support engineer."},
]

while True:
    user_msg = input("> ")
    if user_msg in ("exit", "quit"):
        break
    history.append({"role": "user", "content": user_msg})

    resp = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=history,
    )

    assistant_msg = resp.choices[0].message.content
    print(assistant_msg)
    history.append({"role": "assistant", "content": assistant_msg})
```

For persistent multi-turn conversations, use **sessions** — see [Build Your First Agent](../tutorials/01-first-agent.md) Step 6 for the session pattern.

---

## Step 3 — Streaming responses

Set `stream=True`. The SDK returns an iterator that yields chunks as the LLM generates them:

```python
stream = client.chat.completions.create(
    model="gpt-4o-mini",
    messages=[{"role": "user", "content": "Explain BGP in one paragraph."}],
    stream=True,
)

for chunk in stream:
    delta = chunk.choices[0].delta.content
    if delta is not None:
        print(delta, end="", flush=True)
print()  # newline after streaming finishes
```

Hecate passes `stream=True` through to the underlying provider (LiteLLM). Supported by **all** LiteLLM providers — including local Ollama. To disable token-by-token streaming at the network layer, set `stream_options={"include_usage": False}`.

---

## Step 4 — Function calling / tool use

Define your tools in OpenAI's JSON Schema format. Hecate accepts the standard format:

```python
tools = [
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "Get the current weather for a city",
            "parameters": {
                "type": "object",
                "properties": {
                    "city": {"type": "string", "description": "City name, e.g. 'San Francisco'"}
                },
                "required": ["city"]
            }
        }
    }
]
```

Send the request:

```python
resp = client.chat.completions.create(
    model="gpt-4o-mini",
    messages=[{"role": "user", "content": "What's the weather in Tokyo?"}],
    tools=tools,
    tool_choice="auto",
)

msg = resp.choices[0].message
if msg.tool_calls:
    for call in msg.tool_calls:
        print(f"LLM wants to call: {call.function.name}({call.function.arguments})")
        # YOU implement the tool, then send the result back
        # (Hecate's builtin/MCP/custom tools are surfaced automatically — see below)
```

### Hecate-managed tools (no manual implementation needed)

If your agent already has tools bound (`web_search`, MCP servers, custom tools), Hecate automatically merges them into the OpenAI `tools` array. The LLM picks the right one and Hecate executes it:

```python
# Assumes agent has web_search bound (see Tutorial 01 Step 5)
resp = client.chat.completions.create(
    model="agent/<AGENT_ID>",   # routes to a specific Hecate agent
    messages=[{"role": "user", "content": "What's the latest Python release?"}],
    # No `tools=` arg needed — Hecate injects them automatically
)
print(resp.choices[0].message.content)
```

The `agent/<id>` model name is a Hecate extension to the OpenAI protocol. See [Step 7 — Hecate extensions](#step-7--hecate-extensions-to-the-openai-protocol) below.

---

## Step 5 — Structured outputs

Use OpenAI's `response_format` to force JSON output matching a schema:

```python
from pydantic import BaseModel
from openai import OpenAI

class TodoItem(BaseModel):
    title: str
    priority: int
    due_date: str | None = None

resp = client.beta.chat.completions.parse(
    model="gpt-4o-mini",
    messages=[
        {"role": "system", "content": "Extract todos from the user's message."},
        {"role": "user", "content": "I need to ship the README by Friday and file expenses Monday."},
    ],
    response_format=TodoItem,  # SDK serializes the schema automatically
)

item = resp.choices[0].message.parsed  # A TodoItem instance, not raw JSON
print(item.title, item.priority, item.due_date)
```

Hecate passes `response_format` through to the upstream provider. **Supported by**: OpenAI, Anthropic (via tool use fallback), DeepSeek, Qwen, GLM. **Not supported by**: Ollama and most local models (Hecate returns a 400 — pre-validate with `GET /v1/models` to see what is configured).

---

---

## Step 7 — Hecate extensions to the OpenAI protocol

Hecate supports two extra "model" values beyond standard LLM names. These are extensions to the OpenAI protocol and work with **any** OpenAI-compatible client:

### `agent/<AGENT_ID>` — route to a configured agent

```python
resp = client.chat.completions.create(
    model="agent/a1b2c3d4-e5f6-7890-abcd-ef1234567890",
    messages=[{"role": "user", "content": "Explain this codebase."}],
)
```

Hecate loads the agent's `persona`, `model_config`, `tools`, `skills`, and `knowledge_base_ids`, then dispatches. The response carries the upstream LLM's actual model name in `model` (e.g. `"gpt-4o-mini"`) so client-side billing/logging stays accurate.

### `provider/<PROVIDER_ID>` — bypass agents, use a specific provider config

```python
# Use a pre-configured provider with non-default settings
resp = client.chat.completions.create(
    model="provider/team-gpt4-cheap",
    messages=[{"role": "user", "content": "Hello!"}],
)
```

Configure providers via:

```bash
hecate model provider list
hecate model provider get team-gpt4-cheap
hecate model provider update team-gpt4-cheap --temperature 0.7 --max-tokens 4096
```

Provider configs are managed by admins; useful for routing different requests to different models/cost tiers.

---

## Step 8 — Other compatible clients

The same `base_url` trick works for every library that supports a custom base URL:

| Library | How to switch | Notes |
|---|---|---|
| **`litellm`** | `litellm.completion(model="openai/gpt-4o-mini", api_base="http://localhost:8000/v1", api_key="dev-key-change-me")` | The `openai/` prefix tells litellm to use the OpenAI-format call |
| **`langchain-openai`** | `ChatOpenAI(base_url="http://localhost:8000/v1", api_key="dev-key-change-me")` | Drop-in replacement for `ChatOpenAI()` |
| **`instructor`** | `instructor.from_openai(OpenAI(base_url=..., api_key=...))` | Structured outputs work end-to-end |
| **`vllm` (server mode)** | `--host 0.0.0.0` then point any client at it | Use when you want full control over local inference |
| **`llama-index`** | `OpenAILike(base_url="http://localhost:8000/v1", api_key="...", model="gpt-4o-mini")` | `OpenAILike` accepts arbitrary base URLs |
| **`autogen` / `crewai`** | `llm_config={"base_url": "...", "api_key": "..."}` | Both honor OpenAI's wire protocol |
| **Any raw HTTP client** | Send `POST /v1/chat/completions` with the same JSON shape | Last-resort option |

---

## How it fits together

```
┌──────────────────────────────────────────────────────────────┐
│  Any OpenAI-compatible client                                │
│  (openai-python / litellm / langchain-openai / ...)          │
│                                                              │
│  base_url = http://localhost:8000/v1                          │
│  api_key  = dev-key-change-me (Hecate API key)                │
└──────────────────────┬───────────────────────────────────────┘
                       │  POST /v1/chat/completions
                       │  model: "gpt-4o-mini" | "agent/<id>" | ...
                       ▼
┌──────────────────────────────────────────────────────────────┐
│  Hecate API Layer                                            │
│  ┌────────────────────────────────────────────────────────┐  │
│  │  Resolve model spec                                  │  │
│  │   • "gpt-4o-mini"            → upstream provider       │  │
│  │   • "agent/<id>"             → load Agent config       │  │
│  │   • "provider/<id>"          → load Provider config    │  │
│  └─────────────────────┬──────────────────────────────────┘  │
│                        ▼                                     │
│  ┌────────────────────────────────────────────────────────┐  │
│  │  LiteLLM proxy                                        │  │
│  │  Routes to: OpenAI / Anthropic / DeepSeek / Qwen /  │  │
│  │              Ollama / vLLM / 100+ providers           │  │
│  └─────────────────────┬──────────────────────────────────┘  │
│                        ▼                                     │
│  OpenAI-format response (unchanged shape — drop-in)           │
└──────────────────────────────────────────────────────────────┘
```

---

## Troubleshooting

### `openai.AuthenticationError` (401)

Wrong `api_key`. Confirm it's the **Hecate** API key (`HECATE_API_KEYS=...`), not your OpenAI key. Hecate doesn't validate upstream provider keys against the client request.

### `openai.NotFoundError` (404) on `model: "agent/<id>"`

The agent ID is wrong, soft-deleted, or belongs to a different workspace. Run `hecate agent list` to verify.

### Streaming returns nothing

You forgot `stream=True`. Without it, Hecate buffers the full response before returning. Streaming requires the flag — there's no auto-detection.

### Structured output fails on a local model

Most local models (Ollama, llama.cpp) don't support `response_format`. Either: (1) drop `response_format`, (2) use a prompt-based approach with validation, or (3) route through a hosted model.

### Rate limit hit (429)

Hecate applies per-workspace rate limits. Adjust via `RATE_LIMIT_REQUESTS_PER_MINUTE` in `.env` (default 60) or via the `Rate Limiting` how-to.

---

## Summary

You now know how to:

- **Point any OpenAI client at Hecate** by setting `base_url` to `http://localhost:8000/v1`
- **Use multi-turn chat, streaming, function calling, structured outputs** — all via OpenAI's wire protocol
- **Route to a specific Hecate agent** via the `agent/<id>` extension
- **Use a configured provider** via the `provider/<id>` extension
- **Plug into litellm / langchain-openai / instructor / vllm / llama-index** with the same one-line change

> **Note on embeddings** — Hecate does not expose `/v1/embeddings` as a public endpoint. Embeddings are computed internally when you upload documents to a knowledge base (default model: BGE-M3, 1024-dim). For RAG, see [Knowledge Base and RAG](02-knowledge-base.md). To call an OpenAI embedding model directly, route through your upstream provider (e.g., openai.com) using your existing OpenAI key.

## Next steps

- **[Knowledge Base and RAG](../tutorials/02-knowledge-base.md)** — how Hecate computes embeddings internally when uploading docs to a KB.
- **[MCP Tool Integration](../tutorials/03-mcp-integration.md)** — surface MCP tools to your OpenAI client automatically.
- **[REST API Reference](../reference/rest-api.md)** — every Hecate endpoint, including non-OpenAI ones.
- **[OpenAI API reference](https://platform.openai.com/docs/api-reference)** — Hecate implements the chat, embeddings, and models endpoints; some admin endpoints (agents, knowledge bases) are Hecate-specific.