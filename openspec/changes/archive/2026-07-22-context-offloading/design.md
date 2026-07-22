## Context

Hecate's LLMWorker (engine layer) applies a 4-step context pipeline before each LLM invocation:
1. Tool result truncation (cap each tool result to ~2000 tokens)
2. Token estimation against budget
3. Message selection (`ContextEngine.select_messages`) — keep most recent within budget
4. Compression (`ContextEngine.compress`) — drop oldest as last resort

When step 4 fires, messages are permanently discarded from the LLM's view. The channel retains the originals (non-destructive pipeline), but the LLM has no mechanism to retrieve them — there is no tool, no memory hook, and no file pointer. This contrasts with:

- **AgentScope** — `Offloader` protocol writes oversized context to files and returns a reference
- **Claude Code** — file-based compaction cascade with `read_file` recovery
- **Amazon Bedrock AgentCore** — session state persisted to `/mnt/workspace`, retrievable on resume
- **Letta/MemGPT** — agent self-manages memory via `memory_store`/`memory_search` function tools

Hecate now has `AgentEnvironment` (1.3.15) with `write_file`/`read_file` and per-agent persistent storage under `memory/`. This makes file-based offloading viable without new infrastructure.

**Current state of the pipeline** (`llm_worker.py` L146-182):
```
messages → _truncate_tool_results → estimate_tokens → select_messages → compress → LLM
```

`compress` calls `InMemoryContextEngine.compress()` which does `messages[-max_messages:]` — pure truncation.

**Constraints:**
- Must not break existing ContextEngine ABC (used by other pipelines)
- Must not break ConversationService path (uses CompressionPipeline, not LLMWorker)
- Must work with both LocalEnvironment and DockerEnvironment
- Engine layer has zero external deps — offloader must live in services/ and be passed in via execution_context

## Goals / Non-Goals

**Goals:**
- Preserve overflow context in persistent storage instead of discarding it
- Let the agent retrieve offloaded content on demand via the existing `read_file` tool
- Integrate cleanly with the existing LLMWorker pipeline as a new step before compression
- Remain fully backward compatible — no environment means no offload, fallback to compress
- Keep the offload decision local to LLMWorker (no PregelRuntime orchestration changes beyond context injection)
- Configurable threshold and global enable/disable

**Non-Goals:**
- Semantic search over offloaded messages (future: integrate with L4 Knowledge Memory)
- Automatic re-injection of offloaded content (agent must explicitly `read_file`)
- Modifying the ConversationService path (separate code path, already has CompressionPipeline)
- Changing the ContextEngine ABC (no new abstract methods)
- Summarizing offloaded content via LLM (keep it lossless; summaries are a future enhancement)
- Cross-session offload sharing (each session gets its own offload directory)

## Decisions

### Decision 1: Offloader lives in `services/context/`, not `engine/`

**Choice:** Create `src/hecate/services/context/offloader.py`. LLMWorker receives it via `execution_context["environment"]` (the AgentEnvironment) and constructs the offloader inline, OR receives a pre-built offloader via `execution_context["context_offloader"]`.

**Rationale:** Engine layer has zero external deps (AGENTS.md: "engine/ → Zero external deps"). `AgentEnvironment` is defined in `services/environment/environment.py`. Putting the offloader in engine/ would require importing from services/, a layering violation.

**Alternatives considered:**
- *Put offloader in engine/*: Rejected — layering violation, engine can't import services.
- *Define an Offloader ABC in engine/ports.py*: Overkill for one class; adds extension point we don't need yet.

**Final approach:** LLMWorker consumes `execution_context["environment"]` (an `AgentEnvironment` or None) and calls a helper function from `services/context/offloader.py`. Since `engine/workers/llm_worker.py` already imports from `hecate.engine.context`, but we need services-layer access, the offloader is injected via execution_context as a callable/instance OR we pass the environment and let LLMWorker call a small helper. The cleanest: **pass the environment via execution_context; offloader logic is a pure function in services/ that LLMWorker calls only when environment is present.**

Wait — engine can't import from services. So the offloader must be **passed in** via execution_context, not imported. Decision: `execution_context["context_offloader"]` holds a `ContextOffloader` instance (constructed by whoever wires PregelRuntime — typically the services layer). LLMWorker calls `offloader.offload(messages)` if present. No engine→services import needed.

### Decision 2: Offload triggers BEFORE compression, AFTER selection

**Choice:** Pipeline becomes 5 steps:
```
truncation → estimation → selection → offload → compress (last resort)
```

**Rationale:**
- After selection, we know exactly which messages are being dropped (the ones not selected).
- Offload those specific messages to file, replace with a reference stub.
- Recompute tokens on the [stub + selected] list. If still over budget, THEN compress.
- This means compression (true deletion) only happens when even the stub doesn't fit — extremely rare.

**Alternative considered:** Offload BEFORE selection (offload everything, then select from what remains). Rejected — we'd offload messages that would have been selected anyway, wasting storage and losing context that could have stayed in-line.

### Decision 3: Offloaded messages stored as JSON, not Markdown

**Choice:** Serialize the dropped messages as JSON to `memory/sessions/{session_id}/offloaded_{timestamp}.json`.

**Rationale:**
- JSON preserves message structure (role, content, tool_calls, tool_call_id).
- Markdown would lose tool_calls structure and require a parser to restore.
- The agent's `read_file` returns bytes; JSON is easy to interpret on retrieval.
- Reference stub in the live context is Markdown-formatted for LLM readability.

**Alternatives considered:**
- *Markdown*: Lossy for tool messages. Rejected.
- *MessagePack*: Adds a dependency. JSON is universal and debuggable.
- *One file per message*: Too many files; hard to retrieve as a batch.

### Decision 4: Reference stub format

**Choice:** Replace the offloaded block with a single system-role message:
```
[Earlier conversation (messages 1-{N}) offloaded to {path}.
 Topics: {auto_summary}.
 Use read_file("{path}") to retrieve the full content.]
```

Where `{auto_summary}` is a cheap heuristic summary (first 200 chars of each user message, truncated to 500 chars total) — NOT an LLM summary, to keep offload latency near zero.

**Rationale:**
- System role avoids polluting user/assistant turns.
- Topic hint helps the LLM decide whether retrieval is needed.
- Explicit `read_file` instruction tells the LLM how to recover.
- Heuristic summary avoids an extra LLM call (latency + cost).

**Alternative considered:** No summary, just a pointer. Rejected — LLM has no signal to decide whether retrieval is worth it.

### Decision 5: One offload file per pipeline invocation, not accumulated

**Choice:** Each time the pipeline runs and triggers offload, write a new timestamped file. Do not merge with previous offloads.

**Rationale:**
- Simpler implementation — no read-modify-write of existing offload files.
- Avoids race conditions across concurrent supersteps.
- Each offload is a snapshot of what was dropped at that moment.
- Downside: multiple files accumulate over a long session. Acceptable — agent can read any of them, and a future cleanup task (Session GC agent 13.9b) can prune old offloads.

**Alternative considered:** Single rolling file, append new offloads. Rejected — concurrent superstep writes would corrupt it.

### Decision 6: Config settings

**Choice:**
- `CONTEXT_OFFLOAD_ENABLED: bool = True` — global switch
- `CONTEXT_OFFLOAD_THRESHOLD_TOKENS: int = 6000` — only offload if overflow ≥ this threshold

**Rationale:**
- Threshold prevents offloading trivially small overflows (e.g., 50 tokens over budget → not worth a file write).
- 6000 default ≈ 1500 lines of text — meaningful chunk worth preserving.
- Global switch lets operators disable offload in storage-constrained environments.

### Decision 7: Backward compatibility via execution_context optionality

**Choice:** If `execution_context` lacks `"context_offloader"` or if the offloader has no `AgentEnvironment`, the pipeline skips offload and proceeds to compression exactly as today.

**Rationale:** Zero regression risk. Existing tests, deployments without environments, and the ConversationService path are untouched.

## Risks / Trade-offs

- **[Storage growth]** Each long session accumulates offload files under `memory/sessions/{session_id}/`. → Mitigation: Session GC agent (13.9b) already scans for orphaned data; offload files naturally fall under its scope. Configurable threshold limits frequency.
- **[LLM may not retrieve]** The agent might ignore the offload stub and proceed without the early context, degrading quality. → Mitigation: the topic hint in the stub gives the LLM a signal. Future enhancement: inject a stronger system prompt nudge.
- **[Offload latency]** Writing a JSON file to the environment on every over-budget pipeline invocation adds I/O. → Mitigation: offload only fires when selection drops messages AND overflow ≥ threshold. For LocalEnvironment, file write is sub-millisecond. For DockerEnvironment, tar-based write is slower but only triggers on genuinely long conversations.
- **[No semantic search]** Agent must know the topic to decide whether to retrieve. No vector search over offloaded content. → Accepted trade-off for this change. Future: feed offloaded JSON into L4 Knowledge Memory for semantic retrieval.
- **[Stub tokens still count]** The reference stub consumes some of the budget (~100 tokens). If budget is extremely tight, stub + selected messages might still exceed budget, forcing compression anyway. → Mitigation: stub is capped at 500 chars; compression as last resort is retained.
- **[execution_context contract change]** Adding `"context_offloader"` key is additive. Existing consumers of execution_context are unaffected. → Mitigation: documented in spec; key is optional.

## Migration Plan

No migration required. This is purely additive:
1. Deploy new code with `CONTEXT_OFFLOAD_ENABLED=true` (default).
2. Wire PregelRuntime construction to inject a `ContextOffloader` when an `AgentEnvironment` is available.
3. Existing deployments without environments automatically fall back to the compression-only path.

**Rollback:** Set `CONTEXT_OFFLOAD_ENABLED=false`. Pipeline skips offload step entirely.

## Open Questions

None — all design decisions are resolved. Open questions during implementation will be captured in tasks.md.
