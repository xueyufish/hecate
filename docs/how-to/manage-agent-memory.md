# Manage Agent Memory

How to configure and inspect each of Hecate's four memory levels through the management API. Hecate's memory system is automatic by default — this guide is for when you need to *control* it: set up structured working-memory blocks, look up what L3 remembered about a user, prune or correct stored memories, and check L2 conversation compression.

For the conceptual model (what each level does and when it applies), read [Memory System](../concepts/memory.md) first. All endpoints below are documented in the [REST API reference](../reference/rest-api.md#knowledge-and-memory); this guide assembles them into operational recipes.

---

## L1 — Working memory blocks

Working memory blocks are named regions in the agent's context window (`persona`, `current_task`, `user_profile`, etc.) that the agent reads and edits during a single execution. They live under the agent resource.

### Create a memory block

```bash
curl -X POST http://localhost:8000/api/agents/$AGENT_ID/memory-blocks \
  -H "Authorization: Bearer $HECATE_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "current_task",
    "content": "Helping the user reset their password.",
    "position": 2,
    "token_limit": 200
  }'
```

`name` is how the agent references the block; `position` controls ordering in the prompt; `token_limit` caps how much of the block's content counts toward the context budget.

### List, update, delete

```bash
# List all blocks for an agent
curl http://localhost:8000/api/agents/$AGENT_ID/memory-blocks \
  -H "Authorization: Bearer $HECATE_API_KEY"

# Update a block's content (e.g., the agent advanced to the next step)
curl -X PUT http://localhost:8000/api/agents/$AGENT_ID/memory-blocks/$BLOCK_ID \
  -H "Authorization: Bearer $HECATE_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"content": "Guiding the user through the email verification step."}'

# Remove a block
curl -X DELETE http://localhost:8000/api/agents/$AGENT_ID/memory-blocks/$BLOCK_ID \
  -H "Authorization: Bearer $HECATE_API_KEY"
```

> **L1 is ephemeral.** Blocks live in the context window. When the session ends they are gone unless they were checkpointed or promoted to L3. Use them for state the agent needs *this* execution — not for durable facts.

---

## L3 — User memory (cross-session facts)

L3 holds persistent facts about users (preferences, ongoing projects, established context) extracted automatically as conversations proceed. It is scoped per user + agent + session, so one user's facts never leak to another. Use these endpoints to inspect, search, or correct what the agent has remembered.

### List memories (with filters)

```bash
# All memories in the workspace
curl "http://localhost:8000/api/memory?limit=50" \
  -H "Authorization: Bearer $HECATE_API_KEY"

# Only high-importance procedural memories
curl "http://localhost:8000/api/memory?memory_type=procedural&min_importance=0.7&limit=50" \
  -H "Authorization: Bearer $HECATE_API_KEY"
```

Filters: `memory_type` (e.g. `semantic`, `procedural`, `episodic`), `min_importance` (0.0–1.0), `limit` (1–200).

### Semantic search across a user's memories

```bash
curl "http://localhost:8000/api/users/$USER_ID/memories/search?q=database%20preference&top_k=5&min_importance=0.3" \
  -H "Authorization: Bearer $HECATE_API_KEY"
```

Returns the top-`k` memories ranked by semantic similarity to `q`, filtered by importance. This is how the runtime itself retrieves user context — useful for debugging "why did the agent bring up X?".

### List memories for one user

```bash
curl "http://localhost:8000/api/users/$USER_ID/memories?limit=100" \
  -H "Authorization: Bearer $HECATE_API_KEY"
```

### Correct or remove a memory

If the agent remembered something wrong (or a user asks to be forgotten), delete by ID:

```bash
curl -X DELETE http://localhost:8000/api/memory/$MEMORY_ID \
  -H "Authorization: Bearer $HECATE_API_KEY"
```

> **Privacy tip.** Because L3 is user-scoped, deleting one user's memories never touches another's. For a full "forget me", list then delete that user's memories via `/api/users/{user_id}/memories` + `DELETE`.

---

## L4 — Knowledge memories (per-agent)

L4 is structured knowledge the agent can search at runtime — distinct from the document-based Knowledge Base / RAG pipeline. It supports hybrid (dense + sparse) retrieval and per-memory tags.

### Add knowledge to an agent

```bash
curl -X POST http://localhost:8000/api/agents/$AGENT_ID/knowledge \
  -H "Authorization: Bearer $HECATE_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "content": "The SLA for enterprise tier is 99.95% uptime with 1-hour support response.",
    "tags": ["sla", "enterprise"],
    "importance": 0.9,
    "source": "sales-playbook-v3"
  }'
```

### Hybrid search

```bash
curl -X POST http://localhost:8000/api/agents/$AGENT_ID/knowledge/search \
  -H "Authorization: Bearer $HECATE_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"query": "uptime guarantee enterprise", "top_k": 5, "tags": ["sla"]}'
```

Each result returns a combined `score` plus its `dense_score` and `sparse_score` components, so you can see whether a match was found by semantic similarity, keyword overlap, or both — useful when tuning retrieval quality.

### Filter by tag, delete

```bash
# List only SLA-tagged knowledge
curl "http://localhost:8000/api/agents/$AGENT_ID/knowledge?tags=sla&limit=20" \
  -H "Authorization: Bearer $HECATE_API_KEY"

# Remove outdated knowledge
curl -X DELETE http://localhost:8000/api/agents/$AGENT_ID/knowledge/$MEMORY_ID \
  -H "Authorization: Bearer $HECATE_API_KEY"
```

---

## L2 — Conversation compression

L2 (conversation memory) compresses automatically as the context window fills, through three progressive levels: **snip** → **microcompact** → **autocompact** (see [Memory System](../concepts/memory.md#l2-conversation-memory)). You do not configure it per agent. You can, however, check a session's compression status:

```bash
curl http://localhost:8000/api/sessions/$SESSION_ID/compression \
  -H "Authorization: Bearer $HECATE_API_KEY"
```

The response reports the available compression levels. Note that compression status tracking is being rolled out alongside session persistence — the endpoint may report `compression_applied: false` until that wiring is complete. The compression pipeline itself (snip / microcompact / autocompact) is part of the [Context Engineering](../concepts/context-engineering.md#conversation-compression-l2-memory) layer.

---

## Operational recipes

### "What does the agent know about this user right now?"

```bash
curl "http://localhost:8000/api/users/$USER_ID/memories?limit=50" \
  -H "Authorization: Bearer $HECATE_API_KEY"
```

Review before debugging a personalization issue — the answer often lives here.

### "The agent keeps bringing up an outdated fact."

Find it by semantic search, then delete:

```bash
# Find
curl "http://localhost:8000/api/users/$USER_ID/memories/search?q=outdated%20fact&top_k=5" \
  -H "Authorization: Bearer $HECATE_API_KEY"
# Delete the offending memory_id
curl -X DELETE http://localhost:8000/api/memory/$MEMORY_ID \
  -H "Authorization: Bearer $HECATE_API_KEY"
```

### "Give my agent a fixed set of working state."

Create L1 blocks with sensible defaults at agent setup time (or via a setup script), so every new session starts with the same scaffolding (`current_task`, `constraints`, `user_profile`).

### "Migrate a user's memory between agents."

L3 is scoped per user + agent + session. To copy a user's facts to a new agent, list from the source, then `POST /api/memory` (or insert as L4 knowledge) against the target. There is no built-in cross-agent memory migration endpoint.

---

## See also

- [Memory System](../concepts/memory.md) — the four-level architecture and what each level is for
- [Context Engineering](../concepts/context-engineering.md) — how L2 compression and the per-call context pipeline interact
- [REST API — Knowledge and memory](../reference/rest-api.md#knowledge-and-memory) — the full endpoint reference
- [Knowledge Base and RAG tutorial](../tutorials/02-knowledge-base.md) — the document-based RAG pipeline (distinct from L4 knowledge memories)
