# Use Case: Customer Support Bot

> **30 minutes to build, saves hours of support work daily**

A bot that answers customer questions from your documentation, masks PII before logging, and escalates uncertain cases to a human agent with full context. Combines **RAG + Guardrails + Human-in-the-Loop + MCP**.

---

## The scenario

A SaaS company gets ~500 support tickets/day. The support team wants:

- **70%** of tickets answered automatically within 30 seconds
- **PII** (customer emails, account IDs) never leaves the audit log
- **Confidence** below 0.7 → escalate to a human with the full context

## The architecture

```
┌─────────────────────────────────────────────────────────────────┐
│  Customer question                                            │
│         │                                                       │
│         ▼                                                       │
│  ┌─────────────┐    RAG (knowledge base)                       │
│  │ Support Bot │    → finds top-3 docs by hybrid search         │
│  │  (chat mode)│    → cites sources                            │
│  └──────┬──────┘                                                │
│         │                                                       │
│         │ guardrail hooks (Pre/Post LLM)                       │
│         ▼                                                       │
│  ┌─────────────────┐                                            │
│  │ PII anonymizer  │   masks emails, phones, account IDs        │
│  │ (input + output)│   before logging to PostgreSQL            │
│  └──────┬──────────┘                                            │
│         │                                                       │
│         │ confidence < 0.7  ──▶  HITL interrupt                 │
│         │                          creates Zendesk ticket        │
│         │                          via MCP, routes to human     │
│         ▼                                                       │
│  Answer (with citations)                                       │
└─────────────────────────────────────────────────────────────────┘
```

**Features combined**:

- [Knowledge Base / RAG](../tutorials/02-knowledge-base.md) — vector search over docs
- [Guardrails and Hooks](../tutorials/05-guardrails-hooks.md) — PII anonymization
- [Human-in-the-Loop](../tutorials/06-human-in-the-Loop.md) — confidence-based escalation
- [MCP Tool Integration](../tutorials/03-mcp-integration.md) — Zendesk/Slack ticket creation

---

## 10-line tldr

```bash
# 1. Create a knowledge base from your docs
hecate kb create --name "support-docs" --source ./docs/

# 2. Register the support-bot agent
curl -X POST http://localhost:8000/api/agents -H "Authorization: Bearer dev-key-change-me" \
  -H "Content-Type: application/json" -d '{
    "name": "Support Bot",
    "persona": "You are a customer support engineer...",
    "model_config": {"model": "gpt-4o-mini"},
    "knowledge_base_ids": ["<KB_ID>"],
    "tools": ["mcp_zendesk_create_ticket", "mcp_slack_notify_support"]
  }'

# 3. Register the Zendesk MCP server
curl -X POST http://localhost:8000/api/plugins/create -H "Authorization: Bearer dev-key-change-me" \
  -d '{"manifest": {"name": "zendesk", "type": "mcp", "entry": "mcp://https://zendesk.example.com/mcp"}}'

# 4. Enable PII guardrail hooks + HITL threshold
hecate guardrail set --pre-llm pii-anonymizer --post-llm pii-anonymizer
hecate agent update <AGENT_ID> --confidence-threshold 0.7 --hitl-tool mcp_zendesk_create_ticket

# 5. Customers talk to it via the OpenAI-compatible API
curl -X POST http://localhost:8000/v1/chat/completions \
  -d '{"model": "agent/<AGENT_ID>", "messages": [{"role": "user", "content": "How do I reset my API key?"}]}'
```

---

## The build

### Step 1 — Create the knowledge base

Upload your docs (PDFs, DOCX, Markdown, HTML) to a knowledge base:

```bash
hecate kb create --name "support-docs" --source ./docs/
# → Knowledge base created: kb_8f3c2a1b-9d4e-4f6a-b5c7-1e2d3f4a5b6c

hecate kb list
# → kb_8f3c2a1b-9d4e-4f6a-b5c7-1e2d3f4a5b6c | support-docs | 234 docs | ready
```

Hecate automatically:
- Parses PDFs/DOCX/MD/HTML with Docling
- Chunks at 1000 chars / 200 overlap (sentence-boundary aware)
- Embeds with BGE-M3 (1024-dim dense + sparse)
- Indexes in Qdrant (or Chroma, Milvus — see [Deploy to Production](../how-to/deploy-production.md))

For a deeper dive on RAG, see [Knowledge Base and RAG](../tutorials/02-knowledge-base.md).

### Step 2 — Register the MCP servers

Register an MCP server for Zendesk (or any ticketing system):

```bash
curl -X POST http://localhost:8000/api/plugins/create \
  -H "Authorization: Bearer dev-key-change-me" \
  -H "Content-Type: application/json" \
  -d '{
    "manifest": {
      "name": "zendesk",
      "version": "1.0.0",
      "type": "mcp",
      "entry": "mcp://https://your-zendesk-instance.com/mcp"
    }
  }'
```

Enable it to activate the connection and discover tools:

```bash
curl -X POST http://localhost:8000/api/plugins/<PLUGIN_ID>/enable \
  -H "Authorization: Bearer dev-key-change-me"
```

Hecate fetches the AgentCard and registers every skill as a tool (`mcp_zendesk_<skill>`). Verify:

```bash
hecate tool list --source mcp | grep zendesk
# → mcp_zendesk_create_ticket | mcp | Create a Zendesk ticket
# → mcp_zendesk_update_ticket | mcp | Update a Zendesk ticket
# → mcp_zendesk_search_tickets | mcp | Search Zendesk tickets
```

### Step 3 — Create the agent

```bash
curl -X POST http://localhost:8000/api/agents \
  -H "Authorization: Bearer dev-key-change-me" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Support Bot",
    "persona": "You are a customer support engineer for a SaaS platform. You answer from your knowledge base and cite sources. You always include a confidence level (high/medium/low) in your thinking. If you are not confident, you escalate to a human agent by creating a Zendesk ticket — never guess.",
    "model_config": {"model": "gpt-4o-mini", "temperature": 0.2},
    "knowledge_base_ids": ["kb_8f3c2a1b-9d4e-4f6a-b5c7-1e2d3f4a5b6c"],
    "tools": ["mcp_zendesk_create_ticket", "mcp_zendesk_search_tickets"],
    "skill_ids": ["cite-sources", "ask-clarifying-questions"]
  }'
```

Save the returned `id` — we'll call it `<AGENT_ID>` below.

### Step 4 — Enable guardrails

```bash
hecate guardrail enable --pre-llm pii-anonymizer
hecate guardrail enable --post-llm pii-anonymizer
hecate guardrail enable --tool-call audit-logger
```

Now every LLM call goes through PII anonymization (emails, phone numbers, account IDs masked before logging) and audit logging (every tool call recorded in PostgreSQL).

For custom guardrails (e.g., detect competitor mentions and redirect), see [Guardrails and Hooks](../tutorials/05-guardrails-hooks.md).

### Step 5 — Configure HITL threshold

We want the agent to escalate when it's not confident. The threshold is per-agent:

```bash
hecate agent update <AGENT_ID> --confidence-threshold 0.7 \
  --hitl-tool mcp_zendesk_create_ticket \
  --hitl-template "Customer {{customer_email}} needs help with: {{question}}. Bot confidence: {{confidence}}. {{reasoning}}"
```

When the LLM's self-assessed confidence drops below 0.7, Hecate:
1. Pauses execution at the next checkpoint
2. Calls `mcp_zendesk_create_ticket` with the templated payload
3. Returns the ticket ID to the customer

The human agent gets the ticket with all the bot's reasoning — no need to start from scratch.

### Step 6 — Wire up to your customer-facing channel

Expose Hecate to your web chat widget. The simplest setup:

```javascript
// In your web widget
const resp = await fetch("http://hecate.internal:8000/v1/chat/completions", {
  method: "POST",
  headers: {
    "Authorization": "Bearer <CUSTOMER_FACING_KEY>",
    "Content-Type": "application/json"
  },
  body: JSON.stringify({
    model: "agent/<AGENT_ID>",
    messages: [{role: "user", content: userInput}],
    stream: true  // for token-by-token typing effect
  })
});
```

For Zendesk/Intercom/Slack integrations that call Hecate from the customer-side system, see [Enable A2A Server](../how-to/enable-a2a-server.md).

---

## The evaluation

How do you know the bot is working? Three metrics to track:

### 1. **Auto-resolution rate**

```sql
SELECT
  COUNT(*) FILTER (WHERE ticket_status = 'resolved_without_human') AS auto,
  COUNT(*) FILTER (WHERE ticket_status = 'escalated_to_human') AS escalated,
  COUNT(*) FILTER (WHERE ticket_status = 'human_resolved') AS human_resolved,
  ROUND(100.0 * COUNT(*) FILTER (WHERE ticket_status = 'resolved_without_human') / COUNT(*), 1) AS pct_auto
FROM support_tickets
WHERE created_at > NOW() - INTERVAL '7 days';
```

Target: **>60%** auto-resolved within 30 days of launch.

### 2. **Citation coverage**

How often does the bot cite a source for its answer? Built-in evaluator:

```bash
hecate eval run \
  --dataset support-eval-v1 \
  --evaluator citation-coverage \
  --agent <AGENT_ID> \
  --output eval-results.json
```

Target: **>85%** of answers cite at least one source.

### 3. **PII leak rate**

```bash
hecate audit scan --last-30-days --detect-pii-leaks
```

Target: **zero** PII leaks in the audit log.

For more evaluation patterns, see [Evaluate an Agent](../tutorials/08-agent-evaluation.md).

---

## Adapt it

Common modifications:

| Variation | Change |
|---|---|
| **Multi-language support** | Add a `language-detection` skill; route to a translated KB per language |
| **Voice channel** | Bind a speech-to-text tool; PreLLM hook transcribes before RAG |
| **Proactive suggestions** | Add an async scheduler that triggers the agent when a new doc is published ("Did this answer your question?") |
| **Custom escalation** | Replace Zendesk with a Slack channel via MCP; route VIP customers to a different team |
| **Sentiment-aware escalation** | Add a `sentiment-analysis` skill; escalate negative-sentiment tickets faster |

---

## When NOT to use this pattern

- **Realtime safety-critical support** (medical, financial) — add a human-in-the-loop mandatory step before any advice is sent
- **Highly variable documentation** — if your docs change daily and the bot can't keep up, consider a hybrid (bot suggests, human confirms)
- **Multi-tenant B2B SaaS** — each customer needs their own knowledge base; configure per-workspace RAG (see [Multi-Tenancy](../concepts/multi-tenancy.md))

---

## What's next

- **[Code Review Agent](02-code-review-agent.md)** — similar pattern but for PR review
- **[Research Team](03-research-team.md)** — multi-agent collaboration for long-form research
- **[Knowledge Base / RAG deep dive](../tutorials/02-knowledge-base.md)**
- **[MCP Tool Integration](../tutorials/03-mcp-integration.md)**