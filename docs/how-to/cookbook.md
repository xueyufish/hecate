# Cookbook

Copy-paste recipes for common Hecate patterns. Each recipe is self-contained — grab the one you need. All examples use `curl` against a local Hecate instance (`http://localhost:8000`) with the default dev API key.

> **Prerequisites**: Hecate running (see [Quickstart](../getting-started/quickstart.md)), `DEV_API_KEY` set in `.env`. Replace `dev-key-change-me` with your actual key.

---

## Recipe 1 — Chat agent with web search

An agent that answers questions and can search the web when it doesn't know the answer.

### Step 1 — Configure a search backend

```bash
# .env — pick one provider
SEARCH_PROVIDER=duckduckgo     # no API key needed (default)
# OR
SEARCH_PROVIDER=tavily
SEARCH_API_KEY=tvly-your-key
# OR
SEARCH_PROVIDER=serper
SEARCH_API_KEY=your-serper-key
```

### Step 2 — Create the agent

```bash
curl -X POST http://localhost:8000/api/agents \
  -H "Authorization: Bearer dev-key-change-me" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "web-researcher",
    "description": "Answers questions, searches the web when needed",
    "execution_mode": "chat",
    "model_config": {"model": "gpt-4o-mini"},
    "system_prompt": "You are a research assistant. Use web_search when you need current information. Cite sources.",
    "tools": ["web_search"]
  }'
```

### Step 3 — Chat with it

```bash
curl -X POST http://localhost:8000/v1/chat/completions \
  -H "Authorization: Bearer dev-key-change-me" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "agent/<agent-id-from-step-2>",
    "messages": [{"role": "user", "content": "What are the latest developments in fusion energy?"}]
  }'
```

---

## Recipe 2 — Knowledge-grounded Q&A agent

An agent that answers from your uploaded documents, with citations.

### Step 1 — Create a knowledge base

```bash
curl -X POST http://localhost:8000/api/knowledge-bases \
  -H "Authorization: Bearer dev-key-change-me" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "product-docs",
    "description": "Product manuals and API docs"
  }'
```

### Step 2 — Upload a document

```bash
curl -X POST http://localhost:8000/api/knowledge-bases/<kb-id>/documents \
  -H "Authorization: Bearer dev-key-change-me" \
  -F "file=@./product-manual.pdf"
```

Hecate will parse (Docling), chunk (1000 chars, 200 overlap), embed (BGE-M3), and index (Qdrant) automatically. Check ingestion status:

```bash
curl http://localhost:8000/api/knowledge-bases/<kb-id>/documents \
  -H "Authorization: Bearer dev-key-change-me"
```

### Step 3 — Create an agent bound to the KB

```bash
curl -X POST http://localhost:8000/api/agents \
  -H "Authorization: Bearer dev-key-change-me" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "docs-assistant",
    "description": "Answers questions from product documentation",
    "execution_mode": "chat",
    "model_config": {"model": "gpt-4o"},
    "system_prompt": "Answer questions using only the retrieved context. If the answer is not in the docs, say so.",
    "knowledge_bases": ["<kb-id>"]
  }'
```

The agent now retrieves relevant chunks automatically on every query — no `knowledge-retrieval` node needed in `chat` mode. See [Knowledge and Retrieval](../concepts/knowledge-rag.md).

---

## Recipe 3 — Agent with model fallback

An agent that tries an expensive model first, falls back to a cheaper one on failure.

```bash
curl -X POST http://localhost:8000/api/agents \
  -H "Authorization: Bearer dev-key-change-me" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "resilient-agent",
    "execution_mode": "chat",
    "model_config": {
      "model": "gpt-4o",
      "fallback_models": ["anthropic/claude-3-5-sonnet-20241022", "gpt-4o-mini"]
    },
    "system_prompt": "You are a helpful assistant."
  }'
```

If `gpt-4o` fails (rate limit, timeout, outage), the [Model Hub](../concepts/model-hub.md) tries `anthropic/claude-3-5-sonnet-20241022` next, then `gpt-4o-mini`. The per-provider [circuit breaker](../concepts/model-hub.md#resilience-fallback-and-circuit-breaking) trips on repeated failures to avoid waiting for timeouts.

---

## Recipe 4 — Streaming chat completion

For real-time token-by-token output (chat UIs, voice assistants):

```bash
curl -X POST http://localhost:8000/v1/chat/completions \
  -H "Authorization: Bearer dev-key-change-me" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "agent/<agent-id>",
    "messages": [{"role": "user", "content": "Explain Pregel in three sentences."}],
    "stream": true
  }'
```

The response is Server-Sent Events (SSE) — each `data:` line is a JSON chunk with `delta.content`. The stream ends with `data: [DONE]`. This is the OpenAI-compatible streaming format; any OpenAI SDK client works unmodified.

---

## Recipe 5 — Two-agent handoff workflow

A generalist agent that hands off to a specialist when the question is about billing.

### Step 1 — Create both agents

```bash
# Generalist
curl -X POST http://localhost:8000/api/agents \
  -H "Authorization: Bearer dev-key-change-me" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "triage-agent",
    "execution_mode": "workflow",
    "model_config": {"model": "gpt-4o-mini"},
    "system_prompt": "Route billing questions to the specialist. Answer everything else yourself."
  }'

# Specialist
curl -X POST http://localhost:8000/api/agents \
  -H "Authorization: Bearer dev-key-change-me" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "billing-specialist",
    "execution_mode": "chat",
    "model_config": {"model": "gpt-4o"},
    "system_prompt": "You are a billing expert. Answer invoice, payment, and subscription questions.",
    "knowledge_bases": ["<billing-kb-id>"]
  }'
```

### Step 2 — Use the handoff template

The engine ships a handoff template in `engine/templates.py`. Or define a minimal workflow graph that routes based on the user's question:

```json
{
  "version": "1.0",
  "name": "billing-handoff",
  "state": {
    "messages": {"type": "topic", "reduce": "append", "default": []}
  },
  "nodes": {
    "triage": {
      "type": "conversation",
      "config": {
        "model": "gpt-4o-mini",
        "system_prompt": "If the user asks about billing, invoices, or payments, set handoff=true.",
        "channels": {"readable": ["messages"], "writable": ["messages"]}
      }
    },
    "check_handoff": {
      "type": "condition",
      "config": {
        "routing_mode": "expression",
        "expression": "messages[-1].handoff == true"
      }
    },
    "billing_specialist": {
      "type": "agent",
      "config": {
        "agent_id": "<billing-specialist-id>",
        "channels": {"readable": ["messages"], "writable": ["messages"]}
      }
    }
  },
  "edges": [
    {"source": "__start__", "target": "triage"},
    {"source": "triage", "target": "check_handoff"},
    {"source": "check_handoff", "target": "billing_specialist", "condition": true},
    {"source": "check_handoff", "target": "__end__", "condition": false}
  ],
  "entry": "triage"
}
```

See [Workflows](../concepts/workflows.md) and the [multi-agent tutorial](../tutorials/04-multi-agent.md) for all six collaboration patterns.

---

## Recipe 6 — Workspace tool deny rule

Block a dangerous tool across an entire workspace, regardless of agent config.

```bash
curl -X POST http://localhost:8000/api/tool-policies \
  -H "Authorization: Bearer dev-key-change-me" \
  -H "Content-Type: application/json" \
  -d '{
    "rule_action": "deny",
    "tool_pattern": "execute_code",
    "description": "Block code execution across the workspace"
  }'
```

The `tool_pattern` supports globs — `mcp__github__*` blocks all GitHub MCP tools. Workspace-level `deny` rules cannot be overridden by agent-level configuration. See [Configure tool permissions](configure-tool-permissions.md).

---

## Recipe 7 — Register an external MCP server

Connect Hecate to an external MCP server (e.g., a GitHub MCP server) and use its tools.

```bash
curl -X POST http://localhost:8000/api/mcp/servers \
  -H "Authorization: Bearer dev-key-change-me" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "github",
    "transport": "http",
    "url": "https://mcp.github.dev/sse",
    "auth_type": "bearer",
    "auth_token": "ghp_your_github_token"
  }'
```

Sync the server's tools into Hecate's registry:

```bash
curl -X POST http://localhost:8000/api/mcp/servers/github/sync \
  -H "Authorization: Bearer dev-key-change-me"
```

The discovered tools appear with `source=mcp` and can be bound to any agent:

```bash
curl -X PATCH http://localhost:8000/api/agents/<agent-id> \
  -H "Authorization: Bearer dev-key-change-me" \
  -H "Content-Type: application/json" \
  -d '{
    "tools": ["mcp__github__create_issue", "mcp__github__search_repos"]
  }'
```

See [Tools, MCP, and A2A](../concepts/tools-and-mcp.md).

---

## Recipe 8 — Version and publish an agent

Save an immutable snapshot before making risky changes, then roll back if needed.

```bash
# Save current config as a version
curl -X POST http://localhost:8000/api/agents/<agent-id>/versions \
  -H "Authorization: Bearer dev-key-change-me" \
  -H "Content-Type: application/json" \
  -d '{"description": "v1.2 — before prompt rewrite"}'

# Make your changes (PATCH the agent)...

# If something breaks, roll back:
curl -X POST http://localhost:8000/api/agents/<agent-id>/rollback \
  -H "Authorization: Bearer dev-key-change-me" \
  -H "Content-Type: application/json" \
  -d '{"version": "<version-id>"}'
```

See [Version and roll back an agent](version-and-rollback-agent.md) for the full lifecycle.

---

## Recipe 9 — Use a local model (Ollama)

Run entirely on your own hardware, no external API calls.

```bash
# Start Ollama and pull a model
ollama serve
ollama pull llama3.1

# Point Hecate at it — just use the ollama/ prefix, no API key needed
curl -X POST http://localhost:8000/v1/chat/completions \
  -H "Authorization: Bearer dev-key-change-me" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "ollama/llama3.1",
    "messages": [{"role": "user", "content": "Hello!"}]
  }'
```

Or bind a local model to an agent:

```json
{"model_config": {"model": "ollama/qwen2.5:32b"}}
```

---

## Further reading

- [Tutorials](../tutorials/) — end-to-end guided examples for each subsystem
- [How-to Guides](./) — task-oriented recipes for configuration and operations
- [Graph DSL Reference](../reference/graph-dsl.md) — the full JSON spec for workflow graphs
- [REST API](../reference/rest-api.md) — route map of all four API surfaces
- [Environment Variables](../reference/env-vars.md) — every config variable
