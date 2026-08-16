# How-to: Debug an Agent Run with Execution Replay

> **Vocabulary**: `session` (multi-turn container) → `trace` (one execution, the replay anchor) → `event` (single record). We do NOT use "runId".
> **Substrate**: Built on the event-sourced execution log (Log-as-Truth).

This guide shows you how to use the execution replay UI and the replay REST API to inspect what happened in a session, jump between executions, and time-travel to any point in the log.

---

## 1. Open the replay tab

The replay view is part of the conversation detail page, not a separate dashboard.

**Path**: `Ops Center → Conversations → <session_id>` → tab **"执行回放"** (Execution Replay).

When the tab is **hidden**:

- The session has `log_version = 0` — no event log entries were recorded.
- This is expected for **path A** (agent-tools direct loop) and **path C** (pure-text passthrough) chat sessions. These paths bypass `PregelRuntime`, so they emit no events. The replay tab renders nothing for them.
- Sessions that **do** show the tab: workflow/graph execution, enhanced chat with `kb_ids` / opening remarks / suggestions (path B), scheduled runs, canvas test runs, subgraph invocations.

**Banner you'll always see at the top of the replay view**:

> Replay covers Pregel-path execution only. Path A / path C calls are not in the event log.

That banner is intentional. It exists so users are not misled about what the replay represents.

---

## 2. Read the trace-segment bar

The top of the replay view shows one chip per execution (`trace`):

- **`<trace_id[:8]>` chip with event count badge** — one execution of the session.
- **`unattributed` chip (yellow)** — events without trace correlation (`trace_id=None` or all-zeros). These come from historical data or identity-degenerate runs; treated separately so you don't conflate them with real executions.

A single session can have many chips when the user message → response → next user message loop ran several times, or when the session was interrupted and resumed (each resume is a new trace).

Click a chip to switch the timeline view to that execution's events.

---

## 3. Read the timeline

The timeline lists events in execution order (grouped under the selected trace):

- **Color-coded event type** (`NODE_START` blue, `CHANNEL_WRITE` purple, `LLM_REQUEST` amber, `TOOL_CALL` emerald, `STEP_END` gray, etc.).
- **`v<N> s<M>` label** — version and superstep. Step boundaries (`STEP_END`) are commit points — the engine guarantees state at those versions is replayable.
- **Node ID** when applicable; click any row to open the event detail panel.

When a node fires `CHANNEL_WRITE` for `messages`, the next `LLM_REQUEST` or `TOOL_RESULT` in the same execution window has its **body** (assistant text, tool result content, `is_error` flag) attached. That's how you see what the model actually said, not just the length.

### Guardrail blocks

If a tool call was denied by a guardrail (`PreToolHook` / `PostToolHook`), you'll see a red badge in the timeline with the reason:

> guardrail: PII detected

Phase 1 derives these from synthetic tool-error messages in the `messages` channel (e.g. `"Tool blocked: ..."`). When the planned waterfall middleware ships, the timeline upgrades to consume explicit stage events instead — the API and UI stay the same.

### Subgraph links

`SUBGRAPH_START` events carry a `child_session_id`. Click them in the event detail panel to jump straight into the child session's replay view. Multi-agent debugging becomes a tree walk.

---

## 4. Use the DAG step-through

The DAG view renders the agent's **current** graph topology (React Flow / `@xyflow/react`, already used by the workflow canvas). Selecting a superstep in the timeline:

- **Highlights the nodes** that ran in that step (`NODE_START` / `NODE_END` for the same `node_id`).
- **Highlights channel edges** that received a `CHANNEL_WRITE` in that step.

A banner above the DAG reads:

> Topology is the agent's current graph definition; nodes not in the current definition are flagged below.

If you edited the agent after the run and the previous execution referenced a node that no longer exists, the DAG shows an "unidentified node" placeholder rather than failing. This is by design — version-bound topology (a future enhancement) is the eventual fix; for now we surface the mismatch instead of hiding it.

---

## 5. Time-travel to any commit point

Use the **State Inspector** slider on the right panel:

1. Drag to a version number (0 → max version in the selected trace).
2. The backend folds the event log up to the nearest `STEP_END` commit point `<= N` and returns the channel state snapshot plus the model-visible `messages` list.
3. The response carries `effective_version`, `requested_version`, `fell_back` (true if the requested version wasn't a commit point), and the `commit_points` list.

**What this answers**: "what did the model actually see at step N?" — the answer is `derive_messages()` after folding events up to that point, using the same write path that live mutation uses. No projection drift between replay and execution.

### Errors

- **`404` (session not found)** — usually a stale link or wrong workspace scope. The replay endpoint enforces tenant isolation (you cannot replay across workspaces).
- **`422 NON_REPLAYABLE_PREFIX`** — the log contains events written with a schema version below the current one. This is rare in production but can appear after major engine upgrades; the response includes `stopped_at_version` so you know where the replay breaks.

---

## 6. Use the API directly

If you're scripting or building your own tooling:

```bash
# Timeline (paginated, default limit=100)
curl https://hecate.example.com/api/sessions/$SESSION_ID/replay \
  -H "Authorization: Bearer $TOKEN" \
  | jq '{traces: [.traces[].trace_id], next_cursor, payload_truncated}'

# Detail for one event (full payload, no truncation)
curl "https://hecate.example.com/api/sessions/$SESSION_ID/replay?limit=1&from_version=$V&detail=true" \
  -H "Authorization: Bearer $TOKEN"

# Time-travel at a specific version
curl "https://hecate.example.com/api/sessions/$SESSION_ID/replay/state?at_version=$N" \
  -H "Authorization: Bearer $TOKEN" \
  | jq '.messages, .effective_version, .fell_back'

# Session detail (use log_version to decide whether the replay tab will render)
curl https://hecate.example.com/api/sessions/$SESSION_ID \
  -H "Authorization: Bearer $TOKEN" \
  | jq '.log_version'
```

All endpoints are tenant-scoped (404 on cross-workspace access — not 403, to avoid leaking existence).

---

## 7. Common debugging recipes

### "The agent gave a wrong answer — what did it see?"

1. Open the conversation detail → Execution Replay tab.
2. Select the trace for that turn (last chip).
3. Find the `LLM_REQUEST` event for the offending step; click it. The body preview shows the messages it received.
4. Drag the State Inspector slider to the version just before that LLM call (typically the previous `STEP_END`). The `messages` panel shows the exact conversation state the model saw.
5. Compare against what you expected to be in the context.

### "A tool call was denied — why?"

1. Find the red `guardrail:` badge in the timeline.
2. Click the underlying `TOOL_CALL` (or the `CHANNEL_WRITE` that produced the synthetic error) for the reason text.
3. The reason comes from your `PreToolHook` / `PostToolHook` config (see [Tutorials 05](../tutorials/05-guardrails-hooks.md)).

### "Multi-agent run — which agent did what?"

The replay view shows a `SUBGRAPH_START` row for each agent invocation. Click the row to jump into the child session's own replay. Walk the tree to see the full call sequence.

### "Replaying takes too long for a huge session"

The default `limit=100` covers a typical chat turn. For very long traces, paginate with `from_version` / `next_cursor`. The `payload_truncated` flag tells you when a payload was previewed; pass `detail=true` to fetch the full message body on demand. For 10k+ event sessions, fetch one event at a time with `limit=1` rather than the whole trace.

---

## 8. Coverage boundaries

| Path | What runs | Replay coverage |
|---|---|---|
| Path B (workflow / canvas / enhanced chat with KB or suggestions) | `WorkflowExecutionService` → `PregelRuntime` | ✅ Full |
| Scheduled runs, canvas test runs, agent-with-workflow embeddings | `PregelRuntime` | ✅ Full |
| Subgraph invocations | Independent `PregelRuntime` session | ✅ Full (own replay tree) |
| Path A (`_chat_with_tools`, agent-tools direct loop) | Direct LLM call + tool registry | ❌ No log; tab hidden |
| Path C (pure text passthrough, no KB / suggestions) | Direct `llm_service.chat` | ❌ No log; tab hidden |
| Web chat (`ConversationService`) | Direct LLM loop | ❌ No log; tab hidden |

The empty-tab behavior is intentional — rendering an empty "nothing to show" panel would mislead users into thinking the session was empty. Hiding the tab is the honest UX.

---

## 9. See also

- [Concepts: Sessions](../concepts/sessions.md) — session lifecycle, the materialized cache, log-derived recovery.
- [Concepts: Engine](../concepts/engine.md) — channel system, supersteps, the `fold_session` path.
- [Engine Design](../design/engine-design.md#execution-replay) — how the replay view fits into the runtime.
- [Monitor with OpenTelemetry and Prometheus](monitor-opentelemetry.md) — how OTel traces surface in the replay's `trace_enrichment` block (timing, usage).
- [Troubleshooting guide](troubleshoot.md) — the replay view is the first place to look for any unexpected agent behavior.