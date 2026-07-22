## Why

When conversation context exceeds the token budget, the LLMWorker pipeline currently calls `InMemoryContextEngine.compress()`, which simply discards the oldest messages (`messages[-max_messages:]`). This causes **irreversible information loss**: early user requirements, large tool results, and reasoning chains are permanently dropped, and the LLM has no way to recover them. The existing four-layer memory system (L1 Working / L2 Session / L3 User / L4 Knowledge) handles persistence of *extracted facts*, but does not preserve the raw conversation turns that were filtered out by the engine-layer context pipeline.

We now have `AgentEnvironment` (1.3.15) with `write_file`/`read_file`/`exec_shell` and per-agent persistent storage. This unlocks a superior strategy: instead of deleting overflow context, **offload it to the environment filesystem** and let the agent retrieve it on demand via `read_file` — matching the pattern used by AgentScope (Offloader protocol), Claude Code (file-based compaction), and Amazon Bedrock AgentCore (`/mnt/workspace` session storage).

## What Changes

- **NEW**: `ContextOffloader` class in `services/context/offloader.py` — serializes overflow messages to the AgentEnvironment filesystem (`memory/sessions/{session_id}/offloaded_{timestamp}.json`), returns a compact reference message with a brief summary and `read_file` retrieval hint.
- **MODIFIED**: `LLMWorker._apply_context_pipeline()` — insert an **offload step** between message selection and compression. Offload is attempted first; compression (deletion) is only used as a last resort if offload is unavailable or budget is still exceeded after offload.
- **MODIFIED**: PregelRuntime execution_context — inject the agent's `AgentEnvironment` (when available) so LLMWorker can access environment storage. Propagated via `execution_context["environment"]`.
- **NEW**: Config setting `CONTEXT_OFFLOAD_THRESHOLD_TOKENS` (default 6000) — minimum token overflow that triggers offload. Prevents offloading for trivially small overflows.
- **NEW**: Config setting `CONTEXT_OFFLOAD_ENABLED` (default `true`) — global on/off switch.
- **Backward compatible**: When no `AgentEnvironment` is present in execution_context, the pipeline falls back to the existing `InMemoryContextEngine.compress()` behavior. No environment → no offload → no regression.

## Capabilities

### New Capabilities
- `context-offloading`: A new capability covering the ContextOffloader component — its contract, storage layout, message format, and retrieval semantics. This is a sub-capability of context management, scoped to the offloading mechanism itself.

### Modified Capabilities
- `context-engine`: The `LLMWorker` context pipeline behavior changes — a new offload step is inserted before compression, and the pipeline can now optionally consume an `AgentEnvironment` from `execution_context`. The ContextEngine ABC itself is unchanged; the modification is to how `LLMWorker` orchestrates context_engine + environment.

## Impact

- **Code**:
  - `src/hecate/services/context/offloader.py` (NEW) — ContextOffloader class
  - `src/hecate/engine/workers/llm_worker.py` (MODIFIED) — `_apply_context_pipeline()` gains offload step
  - `src/hecate/engine/pregel.py` (MODIFIED) — execution_context injects environment when available
  - `src/hecate/core/config.py` (MODIFIED) — two new settings
- **APIs**: No external API changes. Internal execution_context contract gains optional `"environment"` key.
- **Dependencies**: No new external dependencies. Reuses existing `AgentEnvironment.write_file()` / `read_file()`.
- **Storage**: Offloaded context stored under `{WORKSPACE_ROOT}/{agent_id}/memory/sessions/{session_id}/offloaded_*.json` (LocalEnvironment) or `/env/memory/sessions/...` (DockerEnvironment).
- **Tests**: New test suite for ContextOffloader and modified LLMWorker pipeline behavior.
