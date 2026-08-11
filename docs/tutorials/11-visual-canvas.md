# Tutorial: Visual Canvas

> **15 minutes** — Design Hecate workflows visually in the browser. Drag nodes onto a canvas, connect them, and export the result as a Hecate workflow that runs through the same Pregel runtime as code-defined agents.

The visual canvas (in `web/`) is a Next.js + React Flow application that provides a no-code alternative to writing Python workflows. It's the same graph DSL you'd write by hand, expressed visually. Anything you can build in `web/`, you can also define programmatically — they're fully equivalent.

This tutorial gets the canvas running locally and walks through building a small multi-agent workflow.

---

## What you will learn

- How the **canvas UI maps to Hecate's graph DSL** (nodes, edges, channels)
- How to **install and run** the canvas in development mode
- How to **build a multi-agent workflow visually** (3 nodes + 2 edges)
- How to **export the canvas to a Hecate workflow** you can invoke via API
- How to **import a Hecate workflow JSON** into the canvas for editing

## Prerequisites

- Hecate running locally (backend) — see [Quickstart](../getting-started/quickstart.md)
- Node.js 20+ and npm or pnpm
- The `web/` directory at the repo root

The canvas is a **separate process** from the Hecate backend — it talks to Hecate via the OpenAI-compatible REST API. Start the backend first, then the canvas.

---

## Step 1 — Understand what the canvas is (and isn't)

| The canvas IS | The canvas IS NOT |
|---|---|
| A visual editor for Hecate workflows | A replacement for the agent definition model |
| Bidirectional with the JSON graph DSL (export/import) | A different execution runtime — exported workflows run on the same Pregel engine |
| Suitable for PMs, designers, and non-developers | Suitable for every workflow — code is still faster for complex logic |
| Free and self-hosted (no SaaS lock-in) | A multi-tenant editor — each canvas instance maps to one Hecate workspace |

The canvas produces the **same JSON DSL** that the [Multi-Agent Orchestration](04-multi-agent.md) tutorial writes by hand. Treat it as a frontend for the DSL, not as a separate product.

---

## Step 2 — Install and start the canvas

```bash
# From the repo root
cd web/

# Install dependencies (Next.js 14, React Flow, etc.)
npm install
# or: pnpm install

# Configure the backend URL
cp .env.example .env.local
# Default points to http://localhost:8000 — adjust if your Hecate runs elsewhere

# Start the dev server (Next.js with hot reload)
npm run dev
```

Open [http://localhost:3000](http://localhost:3000). You should see the canvas landing page:

```
┌──────────────────────────────────────────────────────────────┐
│  Hecate                                          [New] [↻]    │
├──────────────────────────────────────────────────────────────┤
│                                                              │
│  Welcome to the Hecate canvas                                │
│                                                              │
│  [+ New Workflow]   [↥ Import JSON]                          │
│                                                              │
│  Recent workflows:                                           │
│   • customer-support-v2  (last edited 2h ago)                │
│   • research-team         (last edited yesterday)             │
│                                                              │
└──────────────────────────────────────────────────────────────┘
```

> **Port note** — the canvas listens on `3000` (Next.js default), Hecate backend on `8000`. They don't conflict unless you change one.

---

## Step 3 — Build a multi-agent workflow visually

Let's build a 3-node research workflow that any non-developer can create.

### 3a. Create the workflow

Click **+ New Workflow**. Name it `research-team-v1`.

You'll see an empty canvas with a sidebar of node types on the left.

### 3b. Add three agent nodes

Drag the following from the sidebar onto the canvas:

1. **Planner Agent** — position: top-left
   - In the right panel, set `model_config.model = "gpt-4o-mini"`
   - Persona: *"You break down research questions into 2-3 sub-questions."*
2. **Researcher Agent** — position: middle-center
   - `model_config.model = "gpt-4o-mini"`
   - Persona: *"You search the web and synthesize findings. Always cite sources."*
   - Bind the `web_search` tool from the **Tools** panel
3. **Writer Agent** — position: bottom-right
   - `model_config.model = "gpt-4o-mini"`
   - Persona: *"You write a 200-word summary in plain English."*

### 3c. Connect them

Drag from the **bottom handle** of each node to the **top handle** of the next:

```
┌─────────────┐        ┌─────────────────┐        ┌─────────────┐
│   Planner   │───────▶│   Researcher    │───────▶│   Writer    │
│  (decompose)│        │ (search+synth)  │        │  (summarize)│
└─────────────┘        └─────────────────┘        └─────────────┘
```

You now have a directed acyclic graph with 3 nodes and 2 edges.

### 3d. Set the entry node

Click the **Planner** node and toggle **"Is entry"** in the right panel. The Planner is what receives the initial user message.

### 3e. Save

Click **Save** (top-right). The workflow persists to Hecate's database under your workspace.

### Step 3 summary

You just built a workflow that:
1. Takes a user question
2. Plans 2-3 sub-questions
3. Searches the web for each
4. Synthesizes a 200-word summary

…all without writing a line of code.

---

## Step 4 — Export and run via the API

Click **Export → JSON** in the top-right. You get a file like:

```json
{
  "name": "research-team-v1",
  "entry": "planner",
  "nodes": [
    {
      "id": "planner",
      "type": "agent",
      "persona": "You break down research questions into 2-3 sub-questions.",
      "model_config": {"model": "gpt-4o-mini"}
    },
    {
      "id": "researcher",
      "type": "agent",
      "persona": "You search the web and synthesize findings. Always cite sources.",
      "model_config": {"model": "gpt-4o-mini"},
      "tools": ["web_search"]
    },
    {
      "id": "writer",
      "type": "agent",
      "persona": "You write a 200-word summary in plain English.",
      "model_config": {"model": "gpt-4o-mini"}
    }
  ],
  "edges": [
    {"from": "planner", "to": "researcher"},
    {"from": "researcher", "to": "writer"}
  ]
}
```

Import it as a Hecate workflow and invoke it:

```bash
# Import the JSON
curl -X POST http://localhost:8000/api/workflows/import \
  -H "Authorization: Bearer dev-key-change-me" \
  -H "Content-Type: application/json" \
  -d @research-team-v1.json

# Run it via the OpenAI-compatible API (model = "workflow/<id>")
curl -X POST http://localhost:8000/v1/chat/completions \
  -H "Authorization: Bearer dev-key-change-me" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "workflow/<WORKFLOW_ID>",
    "messages": [{"role": "user", "content": "What are the latest advances in retrieval-augmented generation in 2026?"}]
  }'
```

The same workflow definition that the canvas created is now invoked through the runtime — you can run it from any OpenAI-compatible client.

> **Bidirectional** — the JSON export is the source of truth. Editing the JSON in your editor and re-importing it updates the canvas. This means you can version-control workflows in git and review changes via PRs — a workflow that mixes visual + code editing without lock-in.

---

## Step 5 — Import an existing workflow

To edit a workflow that was defined in code, export it from Hecate and import into the canvas:

```bash
# Export a workflow by ID
curl http://localhost:8000/api/workflows/<ID>/export \
  -H "Authorization: Bearer dev-key-change-me" \
  > workflow.json

# In the canvas: Workflows → Import JSON → upload workflow.json
```

The canvas renders it as a graph you can drag, edit, and re-save. Useful when:
- A workflow was authored in code and you want to demo it visually
- You want to iterate visually then commit the JSON export to git
- Multiple people need to collaborate on a workflow (canvas is shareable within a workspace)

---

## Step 6 — Deploy the canvas (production)

For production, build and serve the Next.js app:

```bash
cd web/
npm run build
npm run start
```

Behind a reverse proxy:

```nginx
# nginx example
location /canvas/ {
    proxy_pass http://localhost:3000/;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
}
```

Authenticate canvas requests with the same `HECATE_API_KEYS` you use for the API. The canvas includes a token input field on first load (or you can wire it to your SSO provider — see [Configure SSO and SCIM](../how-to/configure-sso-scim.md)).

---

## How it fits together

```
┌─────────────────────────────────────────────────────────────┐
│  Browser (your dev machine)                                 │
│  ┌─────────────────────────────────────────────────────┐    │
│  │  Hecate Canvas (Next.js, React Flow)               │    │
│  │  http://localhost:3000                              │    │
│  │                                                     │    │
│  │  Drag-and-drop UI  ──▶  exports JSON DSL            │    │
│  │  Imports JSON DSL  ──▶  renders graph               │    │
│  └─────────────────────┬───────────────────────────────┘    │
│                        │ HTTPS / JSON                       │
└────────────────────────┼────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────┐
│  Hecate Backend (this repo, src/hecate/)                    │
│  http://localhost:8000                                       │
│                                                             │
│  ┌──────────────────────────────────────────────────────┐    │
│  │  REST API                                            │    │
│  │  /api/workflows/*  /v1/chat/completions              │    │
│  └─────────────────────┬────────────────────────────────┘    │
│                        ▼                                     │
│  ┌──────────────────────────────────────────────────────┐    │
│  │  Workflow runtime (same engine as code workflows)   │    │
│  │  → Pregel superstep loop                             │    │
│  │  → checkpoint store                                  │    │
│  │  → event log                                         │    │
│  └──────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────┘
```

The canvas and code-defined workflows are **the same engine** — no separate "visual mode" runtime. They share nodes, edges, channels, and execution semantics.

---

## Troubleshooting

### Canvas can't reach the backend

The canvas logs all backend calls in the browser DevTools Network tab. A failed request usually shows `CORS error` or `Failed to fetch`. Fix:

1. Confirm Hecate is running on `http://localhost:8000` (curl it directly: `curl http://localhost:8000/health`)
2. Confirm `web/.env.local` has the right `NEXT_PUBLIC_HECATE_API_URL`
3. If using different origins, ensure Hecate's CORS middleware allows the canvas origin (default: `localhost:3000` is whitelisted)

### Changes don't persist after refresh

The canvas talks to Hecate's workflow API on every save. If the workflow doesn't appear in `hecate workflow list` after saving, the request is failing — check the browser DevTools Network tab for the response body.

### Imported workflow shows nodes but no edges

Edges must use exact node IDs (`from` and `to`). If the JSON has typos or dangling IDs, the canvas renders nodes but skips invalid edges. Validate with `hecate workflow validate <ID>`.

### `npm install` fails on Apple Silicon

React Flow has an optional native dep. If `npm install` fails, try `npm install --no-optional`. The canvas will still work but with reduced rendering performance on very large graphs.

---

## Summary

You now know how to:

- **Run the canvas** alongside the Hecate backend
- **Build a multi-agent workflow visually** by dragging nodes and connecting them
- **Export to JSON** and run via the OpenAI-compatible API
- **Import a code-defined workflow** for visual editing
- **Deploy the canvas** behind a reverse proxy in production

## Next steps

- **[Multi-Agent Orchestration](04-multi-agent.md)** — the same workflows written by hand in Python.
- **[Graph DSL Reference](../reference/graph-dsl.md)** — full schema for the JSON the canvas exports.
- **[web/README.md](https://github.com/xueyufish/hecate/tree/main/web)** — canvas-specific documentation (component list, theme customization, keyboard shortcuts).
- **[Deploy to Production](../how-to/deploy-production.md)** — production deployment including the canvas.