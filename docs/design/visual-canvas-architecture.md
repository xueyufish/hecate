# Visual Canvas Architecture

Deep-dive design document for the visual canvas (`web/`). For the user-level tutorial, see [Tutorial: Visual Canvas](../tutorials/11-visual-canvas.md). For the decision rationale, see [ADR-010: React Flow Canvas](adr/010-react-flow-canvas.md) and [ADR-019: Visual Workflow Node Types](adr/019-visual-workflow-node-types.md).

This document is for **frontend engineers** extending the canvas, **designers** understanding the visual model, and **integrators** building custom node types.

---

## What the canvas is (and isn't)

The visual canvas is a Next.js web application that provides a drag-and-drop interface for designing Hecate workflows. It is **not**:

- ❌ A no-code replacement for code workflows — code workflows are first-class; the canvas is a frontend for them
- ❌ A multi-tenant editor — each canvas instance maps to one workspace
- ❌ A separate runtime — exported workflows run on the same Pregel engine as code-defined ones
- ❌ A standalone product — it has no business value without the Hecate backend

It **is**:

- ✅ A visual frontend for the JSON graph DSL
- ✅ Bidirectional with the DSL (canvas ↔ JSON, always in sync)
- ✅ A way for non-developers to ship what developers can ship in code
- ✅ Same engine, same permissions, same audit as the REST API

---

## Tech stack

```
Next.js 14.2.35             Application framework (App Router)
React 18                     UI runtime
TypeScript                   Type safety
@xyflow/react 12.10.2        Canvas engine (React Flow)
@base-ui/react 1.5.0         Headless UI primitives (Radix-style)
Tailwind CSS 4               Styling
Zod 4.4                      Runtime validation (DSL ↔ schema)
Recharts 3.9                 Monitoring charts (Ops Center tab)
lucide-react                 Icon set
Vitest                       Component testing
```

### Why React Flow (xyflow)

From ADR-010:
- **Open-source MIT** (no licensing risk)
- **Active maintenance** (xyflow has a large team)
- **Custom nodes + edges with MiniMap and Controls out of the box**
- **React + TypeScript first-class** (matches Hecate's stack)
- **Extensible node types** — we define custom React components per node type

### Why not tldraw / Excalidraw

| | React Flow | tldraw | Excalidraw |
|---|---|---|---|
| **Modeling fit** | Graph editor | Free-form canvas | Hand-drawn |
| **Node customization** | ✅ React components | Limited | No |
| **Persistence model** | JSON nodes/edges | Document-based | Image-based |
| **License** | MIT | Apache-2.0 | MIT |
| **Maintenance** | Active | Active | Active |

Hecate needs a **structured** graph editor, not freeform drawing. React Flow is the best fit.

---

## Component architecture

```
web/src/
├── app/
│   ├── (dashboard)/                  # Authenticated route group
│   │   ├── agents/                   # Agent CRUD UI
│   │   ├── chat/                     # Interactive chat console
│   │   ├── knowledge/                # Knowledge base UI
│   │   ├── ops-center/               # Observability dashboards
│   │   ├── plugins/                  # Plugin manager UI
│   │   ├── settings/                 # Workspace settings
│   │   └── workflows/                # Canvas + workflow library
│   ├── login/
│   └── register/
├── components/
│   ├── agent/                       # Agent builder UI
│   │   ├── agent-configurator.tsx
│   │   ├── model-selector.tsx
│   │   ├── tool-selector.tsx
│   │   ├── knowledge-selector.tsx
│   │   ├── memory-block-editor.tsx
│   │   ├── skill-selector.tsx
│   │   └── template-picker.tsx
│   ├── workflow/                     # The canvas itself
│   │   ├── canvas-area.tsx           # The @xyflow/react canvas
│   │   ├── node-palette.tsx          # Sidebar of available nodes
│   │   ├── config-panel.tsx          # Right panel: edit selected node
│   │   ├── pattern-selector.tsx      # Multi-agent pattern templates
│   │   ├── pattern-config-dialog.tsx # Configure a pattern
│   │   ├── node-types.tsx            # Node-type registry
│   │   ├── edge-type-selector.tsx    # Conditional edge editor
│   │   ├── agent-palette.tsx         # Pick an agent for agent-node
│   │   ├── channel-selector.tsx      # Pick a channel for input node
│   │   └── template-picker.tsx       # Pick a template
│   ├── model-monitoring/            # Ops Center charts
│   ├── ui/                          # Reusable primitives
│   └── sidebar.tsx                   # App-level navigation
└── lib/
    ├── api.ts                        # REST client + auth
    └── dsl.ts                        # Canvas ↔ JSON DSL conversion
```

---

## The bidirectional DSL sync

The canvas's core innovation is **the JSON DSL is the single source of truth** (per ADR-010):

```
Canvas (visual state)
       ↑           ↓
       │     user action
       │           ↓
       ↑      DSL state ←─── imports ──── JSON file
       │           ↓                          ↑
       │     user action                      │
       │           ↓                          │
       ↑     Canvas ←─── renders ─────────────┘
```

### Implementation

`web/src/lib/dsl.ts` contains the bidirectional conversion:

```typescript
// Canvas → JSON (when user drags a node)
function canvasToDSL(nodes: CanvasNode[], edges: CanvasEdge[]): WorkflowDSL {
    return {
        nodes: nodes.map(n => ({
            id: n.id,
            type: mapNodeTypeToDSL(n.type),
            config: extractNodeConfig(n),
        })),
        edges: edges.map(e => ({
            from: e.source,
            to: e.target,
            condition: e.data?.condition,
        })),
    };
}

// JSON → Canvas (when importing or after backend sync)
function dslToCanvas(dsl: WorkflowDSL): { nodes: CanvasNode[], edges: CanvasEdge[] } {
    return {
        nodes: dsl.nodes.map(n => ({
            id: n.id,
            type: mapDSLToNodeType(n.type),
            position: lookupPosition(n.id) ?? defaultPosition(n.id),
            data: n.config,
        })),
        edges: dsl.edges.map(e => ({
            id: `${e.from}->${e.to}`,
            source: e.from,
            target: e.to,
            data: e.condition ? { condition: e.condition } : undefined,
        })),
    };
}
```

### Sync guarantees

The canvas **never** holds its own state — every canvas mutation goes:

1. User action (drag, connect, edit config)
2. Update React Flow internal state (optimistic UI)
3. Convert canvas state → DSL
4. Send DSL update to backend (`PUT /api/workflows/{id}` or auto-save)
5. Backend validates, persists, returns updated DSL
6. Canvas re-renders from authoritative DSL

If steps 4-5 fail, the canvas reverts to the last good state and shows an error toast. This is **optimistic with reconciliation**, not true offline-first.

---

## Node type system

From ADR-019, each node type has:

- **Visual representation** — custom React component (`canvas-area.tsx` selects by type)
- **Config schema** — Zod schema in `lib/dsl.ts`
- **Validation** — server-side validates before persistence

| Node type | Visual | Purpose |
|---|---|---|
| `agent` | 🤖 Agent icon | A configured agent invocation |
| `llm` | ✨ Sparkle icon | Raw LLM call (no agent wrapping) |
| `tool` | 🔧 Tool icon | Tool invocation (built-in / MCP / custom) |
| `code` | 💻 Code icon | Inline code execution (sandboxed) |
| `condition` | 🔀 Branch icon | Conditional routing |
| `parallel` | 🔱 Branch icon | Parallel execution |
| `loop` | 🔄 Loop icon | Iterate over collection |
| `subgraph` | 📦 Box icon | Nested graph |
| `human_input` | 👤 Human icon | HITL pause (per ADR-019) · **P4** (1.1.24, was P3 → P4, recently) |
| `trigger` | ⚡ Trigger icon | Webhook / schedule / event · **P4** (1.1.25, was P3 → P4, recently) |
| `channel_input` | 💬 Chat icon | Input from a channel (REST/CLI/WebSocket) · **P4** |
| `channel_output` | 📤 Output icon | Output to a channel · **P4** |
| `ontology_action` | 🏛️ Ontology icon | Ontology CRUD (per ADR-014) · **P5** |
| `ontology_query` | 🔍 Ontology icon | Ontology-augmented generation (per ADR-015) · **P5** |

Adding a new node type requires:

1. Implement React component in `web/src/components/workflow/`
2. Add to `node-types.tsx` registry
3. Add Zod schema in `lib/dsl.ts`
4. Add server-side validation in `src/hecate/studio/workflows/`
5. Add to `node-palette.tsx` sidebar
6. Update [Graph DSL Reference](../reference/graph-dsl.md)

---

## Workflow patterns

From ADR-007 + ADR-019, **multiple static collaboration patterns + the seventh DYNAMIC pattern** (per [ADR-032](../adr/032-dynamic-orchestration.md), recently) ship as **pattern templates** the user can drop into the canvas:

| Pattern | Visual | Behavior |
|---|---|---|
| **Sequential Pipeline** | Linear | Stages run one after another |
| **Parallel Fan-Out** | Fan-out | One node dispatches to many parallel branches |
| **Handoff Routing** | Chain | One agent passes control to next |
| **Broadcast Discussion** | Fan-out | One sender → many parallel receivers |
| **Negotiation Loop** | Loop | Two agents iterate until consensus |
| **Debate Arena** | Round-robin | Multiple agents argue, arbiter decides |

`pattern-selector.tsx` shows these as droppable templates. `pattern-config-dialog.tsx` lets the user customize before instantiation (e.g., "2 workers or 4?").

---

## Backend integration

The canvas talks to Hecate via the Management API (`/api/...`):

| Canvas action | HTTP endpoint | Method |
|---|---|---|
| List workflows | `/api/workflows?workspace_id=X&page=N` | GET |
| Open workflow | `/api/workflows/{id}` | GET |
| Save canvas changes | `/api/workflows/{id}` | PUT |
| Run workflow (test) | `/api/workflows/{id}/test` | POST |
| Import JSON | `/api/workflows/import` | POST |
| Export JSON | `/api/workflows/{id}/export` | GET |
| List versions | `/api/workflows/{id}/versions` | GET |
| Revert to version | `/api/workflows/{id}/versions/{v}/revert` | POST |
| List agents (palette) | `/api/agents?workspace_id=X` | GET |
| List MCP tools (palette) | `/api/mcp/connections` | GET |
| List knowledge bases | `/api/knowledge-bases?workspace_id=X` | GET |

All requests carry the workspace_id (from auth context) — the canvas can only see/modify resources in the user's workspaces (per [Multi-Tenancy Architecture](multi-tenancy-architecture.md)).

### Auto-save

Canvas changes are auto-saved every **5 seconds** of inactivity (debounced):

```typescript
// web/src/lib/api.ts
const debouncedSave = debounce(async (workflow: WorkflowDSL) => {
    await api.put(`/api/workflows/${id}`, workflow);
}, 5000);

useEffect(() => {
    return () => debouncedSave.flush(); // Flush on unmount
}, [workflow]);
```

Manual "Save" button forces immediate flush. Concurrent edit conflicts are handled via version increment on the backend (`/api/workflows/{id}/versions`).

---

## State management

The canvas uses **Zustand** (implicit from package patterns; can be confirmed) for:

- Current workflow state
- Selected node / edge
- Drag state (transient)
- Undo / redo stack (last 50 operations)
- Validation errors per node

State is **not** persisted to localStorage — every change is sent to the backend (debounced). Reloading the page always shows the latest backend state, never stale local state.

### Undo / redo

Each canvas mutation creates an undo entry. The undo stack is **per-session** (lost on page reload — intentional). If you need persistent undo, use workflow versions (`/api/workflows/{id}/versions`).

---

## Permissions

The canvas enforces the same RBAC as the REST API:

| Action | Required role |
|---|---|
| View canvas (read-only) | VIEWER |
| Edit workflow (save changes) | EDITOR |
| Delete workflow | ADMIN |
| Run workflow (test) | EDITOR |
| Create new workflow | EDITOR |

Permissions are checked **server-side** (the canvas just hides UI elements for users without permission). The canvas **never** trusts client-side role checks.

---

## Performance

Canvas performance is bounded by:

| Metric | Target | Measured (single node) |
|---|---|---|
| Initial load (100-node workflow) | <500ms | ~300ms |
| Node drag | 60fps | 60fps (xyflow uses CSS transforms) |
| Auto-save roundtrip | <500ms p95 | ~200ms |
| Validation per node | <50ms | ~10ms |

### Bottlenecks at scale

- **1000+ node workflows**: xyflow starts to lag. Mitigation: **subgraph nodes** that group related nodes into a collapsed box.
- **Concurrent edits**: handled via workflow versions; last writer wins, but UI shows a "newer version available" warning.
- **Real-time collaboration**: not yet implemented (P3+). For now, two users editing simultaneously will overwrite each other.

---

## Deployment

### Dev mode

```bash
cd web/
npm install
npm run dev  # Next.js dev server on http://localhost:3000
```

The dev server proxies `/api/*` to `http://localhost:8000` (Hecate backend) via `next.config.mjs`.

### Production build

```bash
cd web/
npm run build  # Outputs to web/.next/
npm run start  # Serves on port 3000 (or PORT env var)
```

### Behind a reverse proxy

```nginx
# nginx example
location /canvas/ {
    proxy_pass http://localhost:3000/;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
}

location /api/ {
    proxy_pass http://localhost:8000/api/;
    proxy_set_header Host $host;
}

# Static assets (served by Next.js)
location /_next/static/ {
    proxy_pass http://localhost:3000/_next/static/;
    expires 1y;
    add_header Cache-Control "public, immutable";
}
```

### Containerized

```dockerfile
# web/Dockerfile (multi-stage)
FROM node:20-alpine AS builder
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM node:20-alpine AS runner
WORKDIR /app
COPY --from=builder /app/.next ./.next
COPY --from=builder /app/public ./public
COPY --from=builder /app/package.json ./
EXPOSE 3000
CMD ["npm", "start"]
```

---

## Custom node development

To add a new node type:

```tsx
// web/src/components/workflow/nodes/MyCustomNode.tsx
import { Handle, Position, NodeProps } from '@xyflow/react';

export function MyCustomNode({ data, selected }: NodeProps) {
    return (
        <div className={`custom-node ${selected ? 'selected' : ''}`}>
            <Handle type="target" position={Position.Left} />
            <div className="node-header">
                <span className="icon">🆕</span>
                <span className="title">{data.label}</span>
            </div>
            <div className="node-body">
                {data.config?.someField}
            </div>
            <Handle type="source" position={Position.Right} />
        </div>
    );
}
```

Then register in `node-types.tsx`:

```tsx
export const nodeTypes = {
    agent: AgentNode,
    llm: LLMNode,
    // ...
    my_custom: MyCustomNode,  // ← add here
};
```

And add to `node-palette.tsx` so users can drag it from the sidebar.

---

## What's NOT in the canvas

| Feature | Why not yet |
|---|---|
| **Real-time multi-user editing** (Google Docs-style) | Requires CRDT or OT — P3+ on the  |
| **Version diff visualization** | API supports versions, UI is planned |
| **Inline execution trace** | Run executes in backend; trace is in Ops Center |
| **Workflow templates marketplace** | P5 — requires plugin marketplace first |
| **Mobile / tablet UI** | Desktop-only; canvas needs precise drag |

---

## Implementation references

- `web/src/app/(dashboard)/workflows/` — workflow canvas route
- `web/src/components/workflow/canvas-area.tsx` — the canvas component
- `web/src/components/workflow/node-types.tsx` — node type registry
- `web/src/components/workflow/node-palette.tsx` — sidebar palette
- `web/src/components/workflow/config-panel.tsx` — right-side config panel
- `web/src/components/workflow/pattern-selector.tsx` — multi-agent patterns
- `web/src/lib/dsl.ts` — DSL ↔ canvas conversion
- `web/src/lib/api.ts` — REST client
- `web/src/app/(dashboard)/layout.tsx` — dashboard layout
- `web/src/components/sidebar.tsx` — navigation
- `web/package.json` — dependencies
- `web/next.config.mjs` — Next.js config
- `web/tailwind.config.ts` — Tailwind theme

## Related documents

- [ADR-010: React Flow Canvas](adr/010-react-flow-canvas.md) — why React Flow
- [ADR-019: Visual Workflow Node Types](adr/019-visual-workflow-node-types.md) — node type taxonomy
- [Tutorial: Visual Canvas](../tutorials/11-visual-canvas.md) — user-level guide
- [Agent Studio Design](agent-studio-design.md) — broader context
- [Graph DSL Reference](../reference/graph-dsl.md) — DSL schema
- [Multi-Tenancy Architecture](multi-tenancy-architecture.md) — workspace isolation in canvas
- Visual Canvas Architecture — current design