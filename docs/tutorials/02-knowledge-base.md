# Tutorial: Knowledge Base and RAG

> **20 minutes** — Build an agent that answers questions from your own documents using retrieval-augmented generation (RAG). Configure chunking, upload documents, run hit-testing, then chat through an agent that retrieves relevant chunks automatically.

This tutorial picks up where [Build Your First Agent](01-first-agent.md) leaves off. You will learn the knowledge-base data model, document ingestion pipeline, retrieval modes, and how RAG fits into the agent execution loop.

---

## What you will learn

- How a **knowledge base** is configured (embedding model, chunking, retrieval mode)
- How to **create** a knowledge base via REST API and CLI
- How to **upload documents** and watch parsing + embedding complete
- How to **ingest web pages** by URL (crawl + extract)
- How to **hit-test retrieval** with the `/search` and `/compare` endpoints
- How to **attach a KB to an agent** and watch it retrieve before answering
- How to **tune retrieval quality** with `search_mode` and `sparse_weight`

## Prerequisites

- Hecate running locally with PostgreSQL, Qdrant, and MinIO — see [Quickstart](../getting-started/quickstart.md)
- At least one LLM provider configured in `.env`
- `hecate` CLI on your `PATH`
- The `[rag]` extra installed: `uv pip install -e ".[rag]"` (the `sentence-transformers` model downloads ~2.3 GB on first use)

Throughout this tutorial we use `dev-key-change-me` as the API key. Replace it with whatever you set in `HECATE_API_KEYS`.

---

## Step 1 — Understand the knowledge-base model

A knowledge base is a configuration record that controls how documents are **parsed**, **chunked**, **embedded**, and **retrieved**. The KB doesn't store documents directly — it owns a Qdrant collection and metadata about which documents live there.

| Field | Type | Default | Purpose |
|-------|------|---------|---------|
| `name` | string | required | Human-readable identifier |
| `description` | string | — | Optional description |
| `embedding_model` | string | `BAAI/bge-m3` | Sentence-transformer used to embed chunks |
| `chunk_strategy` | enum | `fixed` | `auto` / `fixed` / `semantic` |
| `chunk_size` | int | `512` | Target chunk size in tokens (128–2048) |
| `chunk_overlap` | int | `100` | Overlap between adjacent chunks (0–512) |
| `search_mode` | enum | `hybrid` | `hybrid` / `dense` / `sparse` |
| `sparse_weight` | float | `0.3` | Sparse score weight in hybrid mode (0.0–1.0) |
| `collection_name` | string | auto-generated | The Qdrant collection backing this KB |

### Chunking strategies

| Strategy | Behavior | When to use |
|----------|----------|-------------|
| **`auto`** | Adaptive splitting based on document structure (headings, paragraphs) | Mixed-content documents |
| **`fixed`** | Fixed-size tokens with overlap | Long homogeneous text (logs, articles) |
| **`semantic`** | Splits at semantic boundaries detected by similarity shifts | Content with natural topic boundaries |

### Retrieval modes

| Mode | Behavior | When to use |
|------|----------|-------------|
| **`hybrid`** | Combines dense (vector) + sparse (BM25-style keyword) scores | **Default.** Best for most cases — robust to both phrasing and exact terms. |
| **`dense`** | Vector similarity only | When your query vocabulary matches document vocabulary well |
| **`sparse`** | Keyword matching only | When exact terms matter (product codes, error messages) |

In hybrid mode, `sparse_weight` controls the mix: `0.0` = pure dense, `1.0` = pure sparse.

---

## Step 2 — Create a knowledge base (REST API)

Create a KB configured for technical documentation — moderate chunk size with semantic splitting:

```bash
curl -X POST http://localhost:8000/api/knowledge-bases \
  -H "Authorization: Bearer dev-key-change-me" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Hecate Documentation",
    "description": "Internal docs and ADRs for RAG-powered support agent",
    "embedding_model": "BAAI/bge-m3",
    "chunk_strategy": "semantic",
    "chunk_size": 512,
    "chunk_overlap": 100,
    "search_mode": "hybrid",
    "sparse_weight": 0.3
  }'
```

The response includes the new KB's `id` and an auto-generated `collection_name`:

```json
{
  "id": "8f2e1a3b-4c5d-6e7f-8901-abcdef012345",
  "name": "Hecate Documentation",
  "description": "Internal docs and ADRs for RAG-powered support agent",
  "embedding_model": "BAAI/bge-m3",
  "chunk_strategy": "semantic",
  "chunk_size": 512,
  "chunk_overlap": 100,
  "collection_name": "kb_8f2e1a3b4c5d",
  "search_mode": "hybrid",
  "sparse_weight": 0.3,
  "created_at": "2026-01-15T10:30:00Z"
}
```

Copy the `id` — you'll use it in every subsequent step.

### First-time model download

On the first KB creation or document upload, Hecate downloads `BAAI/bge-m3` (~2.3 GB) into its local model cache. Subsequent restarts use the cached model. Watch the logs:

```
Downloading model 'BAAI/bge-m3' to ~/.cache/huggingface/...
```

---

## Step 3 — Create a knowledge base (CLI)

For one-liners:

```bash
hecate kb create \
  --name "Hecate Documentation" \
  --description "Internal docs and ADRs for RAG-powered support agent" \
  --embedding-model "BAAI/bge-m3" \
  --chunk-strategy semantic
```

List your KBs:

```bash
hecate kb list
```

```
┏━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━┓
┃ id                   ┃ name                   ┃ search_mode ┃
┡━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━┩
│ 8f2e1a3b-4c5d-...   │ Hecate Documentation   │ hybrid      │
└──────────────────────┴────────────────────────┴────────────┘
```

---

## Step 4 — Upload documents

Hecate accepts documents via `multipart/form-data` upload (file contents go to MinIO) or URL crawling.

### Upload a local file

Save some sample text (or use any existing `.md`, `.txt`, or `.pdf`):

```bash
cat > /tmp/hecate-overview.md << 'EOF'
# Hecate Agent Platform

Hecate is an enterprise-grade, multi-tenant Agent platform with a self-developed Pregel execution runtime.

## Architecture

Hecate uses a five-layer architecture: engine, services, api, models, core. The engine layer has zero external dependencies and defines 26 extension interfaces + multiple plugin SPI types.

## Multi-tenancy

Each organization contains multiple workspaces. RBAC controls access per workspace. Agents and knowledge bases are scoped to a workspace.

## Deployment

Docker Compose is the reference deployment. For zero-downtime deploys, use the blue-green template with nginx routing traffic between blue/green instances.
EOF

# Upload it
hecate kb upload 8f2e1a3b-4c5d-6e7f-8901-abcdef012345 /tmp/hecate-overview.md
```

Or via API with curl:

```bash
curl -X POST http://localhost:8000/api/knowledge-bases/8f2e1a3b-4c5d-6e7f-8901-abcdef012345/documents \
  -H "Authorization: Bearer dev-key-change-me" \
  -F "file=@/tmp/hecate-overview.md;type=text/markdown"
```

The upload creates a `DocumentModel` with `parsing_status: "pending"`. A background worker parses the file, splits it into chunks, embeds each chunk, and writes embeddings to Qdrant. Status transitions: `pending` → `completed` (or `failed` on error).

### Wait for parsing to complete

```bash
hecate kb documents 8f2e1a3b-4c5d-6e7f-8901-abcdef012345
```

The CLI polls; rerun it manually until `parsing_status` is `completed` and `chunk_count > 0`:

```
┏━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━┳━━━━━━━━━━━━━━┓
┃ id               ┃ filename               ┃ status    ┃ chunk_count  ┃
┡━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━╇━━━━━━━━━━━━━━┩
│ 1a2b3c4d-...    │ hecate-overview.md     │ completed │ 12           │
└──────────────────┴────────────────────────┴───────────┴──────────────┘
```

> **Why a background worker?** Embedding is CPU/GPU-intensive. The upload endpoint returns immediately with a `pending` document; the worker pipeline processes it asynchronously. For production, set up alerts on `parsing_status = "failed"` rows.

---

## Step 5 — Ingest web pages by URL

Skip the file upload for content that's already on the web. Hecate crawls the URL, extracts text, and ingests as a document:

```bash
curl -X POST http://localhost:8000/api/knowledge-bases/8f2e1a3b-4c5f-6e7f-8901-abcdef012345/urls \
  -H "Authorization: Bearer dev-key-change-me" \
  -H "Content-Type: application/json" \
  -d '{
    "urls": [
      "https://hecate.example.com/docs/getting-started/quickstart",
      "https://hecate.example.com/docs/concepts/overview"
    ]
  }'
```

Response:

```json
{
  "ingested": [
    {
      "document_id": "uuid-1",
      "url": "https://hecate.example.com/docs/getting-started/quickstart",
      "title": "Quickstart",
      "chunk_count": 8
    },
    {
      "document_id": "uuid-2",
      "url": "https://hecate.example.com/docs/concepts/overview",
      "title": "Architecture Overview",
      "chunk_count": 14
    }
  ],
  "errors": []
}
```

URL-ingested documents skip the pending state — `parsing_status` is `completed` immediately because the crawl returns parsed text.

---

## Step 6 — Hit-test retrieval

Before attaching the KB to an agent, test that retrieval actually surfaces the chunks you expect. The `/search` endpoint returns scored hits:

```bash
curl -X POST http://localhost:8000/api/knowledge-bases/8f2e1a3b-4c5d-6e7f-8901-abcdef012345/search \
  -H "Authorization: Bearer dev-key-change-me" \
  -H "Content-Type: application/json" \
  -d '{
    "query": "How does Hecate achieve zero-downtime deployment?",
    "mode": "hybrid",
    "limit": 3
  }'
```

Response:

```json
{
  "query": "How does Hecate achieve zero-downtime deployment?",
  "mode": "hybrid",
  "total": 3,
  "results": [
    {
      "id": "chunk-uuid-1",
      "score": 0.87,
      "dense_score": 0.82,
      "sparse_score": 0.94,
      "content": "For zero-downtime deploys, use the blue-green template with nginx routing traffic between blue/green instances...",
      "metadata": {
        "source_url": "https://hecate.example.com/docs/getting-started/quickstart",
        "title": "Quickstart",
        "filename": "hecate-overview.md"
      }
    },
    ...
  ]
}
```

`score` is the combined retrieval score. `dense_score` and `sparse_score` show the per-mode contribution — useful for tuning `sparse_weight`.

### A/B compare retrieval modes

If you're not sure whether `hybrid`, `dense`, or `sparse` works best for your content, run all three on the same query:

```bash
curl -X POST http://localhost:8000/api/knowledge-bases/8f2e1a3b-4c5d-6e7f-8901-abcdef012345/compare \
  -H "Authorization: Bearer dev-key-change-me" \
  -H "Content-Type: application/json" \
  -d '{
    "query": "What extension points does the engine expose?",
    "limit": 3
  }'
```

Returns results side-by-side from each mode so you can eyeball the quality difference.

---

## Step 7 — Attach the KB to an agent

Create or update an agent to reference this KB:

```bash
curl -X POST http://localhost:8000/api/agents \
  -H "Authorization: Bearer dev-key-change-me" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Docs Support Agent",
    "persona": "You are a precise documentation assistant. When asked a question, retrieve the relevant chunks from your knowledge base and answer strictly from what you found. If the answer is not in the knowledge base, say so explicitly.",
    "model_config": {"model": "gpt-4o-mini", "temperature": 0.2},
    "mode": "chat",
    "knowledge_base_ids": ["8f2e1a3b-4c5d-6e7f-8901-abcdef012345"]
  }'
```

Copy the new agent's `id` and chat with it via the standard `/v1/agents/<AGENT_ID>/chat/completions` endpoint (same as Tutorial 01):

```bash
curl -X POST http://localhost:8000/v1/chat/completions \
  -H "Authorization: Bearer dev-key-change-me" \
  -H "Content-Type: application/json" \
  -d '{
    "messages": [
      {"role": "user", "content": "How does Hecate achieve zero-downtime deployment?"
    ]
  }'
```

Hecate's Pregel runtime inserts a `KnowledgeWorker` step before the LLM call. The worker retrieves the top-K chunks from the agent's `knowledge_base_ids` using the KB's configured `search_mode` and `sparse_weight`, then injects them into the LLM prompt as context.

### What the agent sees

The LLM receives something like:

```
[System]
You are a precise documentation assistant. ...

[Retrieved context — top 3 chunks from knowledge base]
1. (score 0.87) "For zero-downtime deploys, use the blue-green template..."
2. (score 0.81) "Hecate uses a five-layer architecture: engine, services, api, models, core..."
3. (score 0.75) "Docker Compose is the reference deployment..."

[User]
How does Hecate achieve zero-downtime deployment?

[Assistant]
Hecate achieves zero-downtime deployment via blue-green templates with nginx routing...
```

The `KnowledgeWorker` runs in parallel with other engine workers — RAG latency adds roughly 50–200ms depending on embedding model and Qdrant load.

---

## Step 8 — Override KBs per request

Sometimes an agent should answer from a different KB depending on the conversation. Pass `kb_ids` directly in the chat request to override:

```bash
curl -X POST http://localhost:8000/v1/agents/<AGENT_ID>/chat/completions \
  -H "Authorization: Bearer dev-key-change-me" \
  -H "Content-Type: application/json" \
  -d '{
    "messages": [{"role": "user", "content": "What does the engine ADR-005 say about worker pools?"],
    "kb_ids": ["<some-other-kb-id>"]
  }'
```

This bypasses the agent's `knowledge_base_ids` and uses only the KBs in `kb_ids`. Useful for routing different question types to specialized KBs.

> The `kb_ids` field triggers the **enhanced execution path** in `/v1/chat/completions`, which runs through `WorkflowExecutionService` + Pregel. For pure LLM calls without retrieval, omit both `kb_ids` and the agent's `knowledge_base_ids`.

---

## Step 9 — Inspect stored chunks

For debugging "why did the agent retrieve this chunk", list what's actually in the collection:

```bash
curl -H "Authorization: Bearer dev-key-change-me" \
  "http://localhost:8000/api/knowledge-bases/8f2e1a3b-4c5d-6e7f-8901-abcdef012345/chunks?page=1&page_size=20"
```

Each chunk returns its content, metadata (source filename, URL, page number), and the embedding-vector ID. Useful when tuning `chunk_strategy` — if you see semantically unrelated chunks adjacent in the source document, the strategy needs adjustment.

---

## How RAG fits into the engine

```
┌──────────────────────────────────────────────────────────┐
│  POST /v1/chat/completions  with kb_ids OR agent KBs      │
└──────────────────────┬───────────────────────────────────┘
                       ▼
┌──────────────────────────────────────────────────────────┐
│  Pregel Runtime (Superstep Loop)                         │
│                                                          │
│   ┌────────────────┐     ┌──────────┐     ┌───────────┐  │
│   │ KnowledgeWorker│────▶│  LLM     │────▶│ Response  │  │
│   │ (retrieve top- │     │ (with    │     │           │  │
│   │  K chunks)     │◀────│  chunks  │     │           │  │
│   └────────────────┘     │  in ctx) │     └───────────┘  │
│           │              └──────────┘                    │
│           ▼                                              │
│   Qdrant vector search                                   │
│   + BM25 sparse index                                    │
└──────────────────────────────────────────────────────────┘
```

The `KnowledgeWorker` runs in parallel with other engine steps when possible. Result: chunks arrive in the LLM context before the LLM call fires, with no extra latency beyond the retrieval itself.

---

## Tuning retrieval quality

If the agent is hallucinating or returning irrelevant chunks:

1. **Switch chunk strategy** — `auto` works for most; try `semantic` if paragraphs are getting split mid-thought.
2. **Increase `chunk_size`** — 512 is conservative; 1024 captures more context per chunk but reduces total count.
3. **Switch search mode** — if queries have specific terminology (error codes, product names), `sparse` or higher `sparse_weight` may help.
4. **Check embedding model fit** — `BAAI/bge-m3` is multilingual and works for English + Chinese + code. For pure English prose, `BAAI/bge-large-en-v1.5` may produce better dense scores.

After each change, rerun `/search` and `/compare` to see if quality improves before testing with the agent.

---

## Troubleshooting

### Document stuck in `parsing_status: "pending"`

The background worker isn't running or crashed. Check Hecate logs:

```bash
docker compose -f docker/docker-compose.yml logs hecate | grep -i "parse\|embed"
```

Common causes:
- **Embedding model download failed** — first-time downloads require outbound HTTPS to `huggingface.co`
- **Qdrant unreachable** — check `QDRANT_URL` and that the Qdrant container is `(healthy)`
- **MinIO unreachable** — uploaded files live in MinIO; the worker needs to read them back during parsing

### `search` returns empty results for queries that should match

- **Embedding model mismatch** — if you changed `embedding_model` after creation, the collection still has old embeddings. Delete and recreate the KB.
- **Chunks too small** — if `chunk_size=128`, individual chunks may lack context. Increase to 512.
- **Wrong collection** — verify the `collection_name` matches what's actually in Qdrant: `curl http://localhost:6333/collections`

### Agent hallucinates despite the KB

- **Persona is too permissive** — add explicit "answer only from the retrieved context" instruction
- **`temperature` is high** — for RAG, use `temperature: 0.0–0.3` to avoid creative additions
- **Top-K too low** — the default retrieval K may be too small; check engine settings for `KB_TOP_K` or equivalent

### RAG latency is high (>1s)

- **First query after restart** — embedding model loads on first call; subsequent calls are fast
- **Qdrant under load** — check Qdrant metrics; consider a dedicated Qdrant instance for production
- **Large chunks + long overlap** — `chunk_size=2048` with `chunk_overlap=512` produces large embeddings; tune down

### `compare` shows dense scores much higher than sparse (or vice versa)

This is normal — different content favors different retrieval. If hybrid consistently produces the best answers for your query type, lock in that `sparse_weight` per KB. If neither dense nor sparse is hitting, the issue is chunking, not scoring.

---

## Summary

You now know how to:

- **Create knowledge bases** with configurable chunking, embedding, and retrieval
- **Ingest documents** via file upload or URL crawling
- **Hit-test retrieval** with `/search` and `/compare` endpoints
- **Attach KBs to agents** so the Pregel runtime retrieves before answering
- **Override KBs per request** via the `kb_ids` chat parameter
- **Tune retrieval quality** by adjusting chunk strategy and search mode

## Next steps

- **[MCP Tool Integration](03-mcp-integration.md)** — connect Hecate to external MCP servers as a tool provider or expose Hecate itself as an MCP server.
- **[Multi-Agent Orchestration](04-multi-agent.md)** — build workflows where multiple agents collaborate, each with their own KBs.
- **[Enable MCP Server](../how-to/enable-mcp-server.md)** — expose KB operations (`knowledge_search`, `knowledge_ingest`) as MCP tools for Claude Desktop.
- **[RAG Pipeline Design](../design/rag-pipeline-design.md)** — architecture-level details on the parsing, chunking, embedding, and retrieval stages.
- **[Tutorial: Build Your First Agent](01-first-agent.md)** — back to basics if you skipped ahead.