## Why

The `services/orchestration/handoff.py` module shipped in 2026-06 (P2 agent-communication-and-routing) is dead code: its functions (`inject_handoff_tools`, `is_handoff_tool_call`, `create_handoff_worker_result`) have no callers anywhere in `src/hecate/`. The DSL schema, parser, and compiler support `trigger="handoff"` and `trigger="dynamic_handoff"`, but no worker ever injects the `handoff_to_agent` tool, detects when the LLM calls it, or returns `Command(goto=...)`. The Pregel runtime already honors `Command(goto=...)` (pregel.py:502) — the wiring just stops one layer short. Meanwhile, industry frameworks (OpenAI Agents SDK, Google ADK, LangGraph, AutoGen) have converged on "handoff as special tool call" plus structured context passing, both of which Hecate lacks.

## What Changes

- **Wire `handoff.py` into the execution path**: `AgentExecutionPort.agent_execute()` injects the `handoff_to_agent` tool when the calling AGENT node has outgoing handoff edges; `AgentWorker` reads handoff signal from the port's result dict and returns `WorkerResult(command=Command(goto=target))`.
- **Add `context_mode` to handoff**: `inherited` (default, full history — matches OpenAI Swarm), `isolated` (fresh context — matches Claude Code subagent), `summarized` (collapsed summary — matches OpenAI `nest_handoff_history`). Stored in node config alongside `invocation_mode`.
- **Fix tool-call pairing on handoff**: when the LLM calls `handoff_to_agent`, the resulting channel update MUST include the `AIMessage(tool_call)` paired with a synthetic `ToolMessage(ack)` so the next agent sees a valid conversation history (LangGraph contract).
- **Per-target handoff tool descriptions**: instead of one generic description, each injected handoff tool carries the target agent's `description`/`handoff_description` so the LLM can route accurately (matches Agents SDK `handoff_description`, ADK `agent.description`, Agentforce `go_to_X description`).
- **`PregelRuntime` populates `handoff_targets` in `execution_context`**: workers need to know which targets are valid without seeing the full compiled graph. The runtime inspects outgoing edges and passes a list to the worker.
- **Optional `OnHandoffHook`** (deferred to a follow-up if scope grows): a guardrail-style hook fired when a handoff is invoked, enabling side effects (telemetry, prefetch, auth check). The existing PreLLMHook/PostLLMHook framework is the template.

## Capabilities

### New Capabilities
<!-- None — this change extends an existing capability. -->

### Modified Capabilities
- `agent-handoff`: Wire the existing handoff module into the execution path so `Command(goto=...)` is actually produced. Add `context_mode` (inherited/isolated/summarized) for downstream-agent context engineering. Require AIMessage+ToolMessage pairing on handoff completion. Per-target handoff tool descriptions.

## Impact

- **`src/hecate/services/orchestration/agent_execution_port.py`** — inject `handoff_to_agent` tool when handoff edges exist; detect tool call in LLM response; return `handoff_to` in result dict; apply `context_mode` filtering to messages before LLM call.
- **`src/hecate/engine/workers/agent_worker.py`** — read `handoff_to` from port result, translate to `WorkerResult(command=Command(goto=...))`; pair `AIMessage` + `ToolMessage` in channel_updates.
- **`src/hecate/engine/pregel.py`** — in `_dispatch_node` / worker invocation, populate `execution_context["handoff_targets"]` from outgoing handoff/dynamic_handoff edges.
- **`src/hecate/engine/graph-dsl.schema.json`** — add optional `handoff` object on AGENT node config: `{context_mode: "inherited"|"isolated"|"summarized", description?: string}`.
- **`src/hecate/services/orchestration/handoff.py`** — extend `build_handoff_tool_schema` to accept per-target descriptions; add `filter_messages_for_handoff(messages, context_mode)` helper.
- **`tests/test_services/test_orchestration/test_agent_execution_port.py`** — add tests for handoff tool injection, handoff detection, context_mode filtering.
- **`tests/test_engine/test_handoff.py`** — add integration-style tests that exercise `AgentWorker` → `AgentExecutionPort` → `Command(goto=...)` end-to-end via Pregel runtime.
- **`docs/design/engine-design.md`** — document the handoff execution path and the three `context_mode` strategies.

No breaking DSL changes: graphs without `handoff.context_mode` continue to work with `inherited` as the default.
