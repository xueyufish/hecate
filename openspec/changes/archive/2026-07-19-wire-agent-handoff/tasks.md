## 1. DSL & Compiler

- [x] 1.1 Add optional `handoff` object to AGENT node config in `src/hecate/engine/graph-dsl.schema.json` with fields `context_mode` (enum: `"inherited"` | `"isolated"` | `"summarized"`, default `"inherited"`) and `description` (optional string)
- [x] 1.2 Extend `GraphCompiler._validate_*` (or add `_validate_agent_handoff_config`) in `src/hecate/engine/compiler.py` to reject AGENT nodes with invalid `handoff.context_mode` values via `GraphValidationError`

## 2. PregelRuntime: populate handoff_targets in execution_context

- [x] 2.1 In `src/hecate/engine/pregel.py`, locate the worker dispatch site (likely `_dispatch_node` or the worker pool invocation)
- [x] 2.2 Before dispatching, if the node type is AGENT and the compiled graph has outgoing edges with `trigger` of `"handoff"` or `"dynamic_handoff"`, build a list of `{"node_id": str, "description": str}` dicts (resolve description from target AgentModel.description first, target node `name` as fallback)
- [x] 2.3 Inject this list as `execution_context["handoff_targets"]` before passing to `worker.execute(...)`
- [x] 2.4 For non-AGENT nodes OR AGENT nodes without handoff edges, leave `handoff_targets` absent (do not write an empty key — keeps the contract clean)

## 3. AgentExecutionPort: inject handoff tool + detect handoff call

- [x] 3.1 In `src/hecate/services/orchestration/agent_execution_port.py`, extend `agent_execute()` to accept `handoff_targets` from `context` parameter (already passed via `AgentWorker._handle_direct_mode`)
- [x] 3.2 If `handoff_targets` is non-empty, call `inject_handoff_tools(tools=[], compiled=None, node_id=...)` — refactor `inject_handoff_tools` to accept a `targets` list directly instead of requiring a CompiledGraph (add new helper signature, keep old one for back-compat)
- [x] 3.3 Extend `build_handoff_tool_schema` in `src/hecate/services/orchestration/handoff.py` to accept an optional `descriptions_by_target: dict[str, str] | None = None`; when provided, format the tool description as `"Transfer to a specialist agent. Available targets:\n- {target}: {description}\n..."`
- [x] 3.4 After the LLM responds, scan the response for a tool call whose name is `handoff_to_agent` (use existing `is_handoff_tool_call()`)
- [x] 3.5 If found, call `validate_handoff_target()` against the requested target; on invalid target, return `{"response": "<error message for LLM, suggesting retry>", "usage": {...}}` without `handoff_to`
- [x] 3.6 If valid, return `{"response": "", "handoff_to": "<target_node_id>", "usage": {...}, "_handoff_tool_call_id": "<original tool_call_id>", "_handoff_messages_snapshot": <messages at handoff time>}` — extra keys for the worker to use when building the channel update

## 4. AgentWorker: translate handoff_to into Command(goto=...)

- [x] 4.1 In `src/hecate/engine/workers/agent_worker.py` `_handle_direct_mode`, after `port.agent_execute(...)` returns, check for the `handoff_to` key
- [x] 4.2 If present, read `handoff.context_mode` from `node_config` (default `"inherited"`)
- [x] 4.3 Call a new helper `build_handoff_channel_updates(...)` (in `handoff.py`) that produces the correctly-paired `messages` list:
  - `inherited` → `[*messages_at_handoff, aimessage_with_tool_call, toolmessage_ack]`
  - `isolated` → `[system_note, aimessage_with_tool_call, toolmessage_ack]`
  - `summarized` → `[system_summary_message, aimessage_with_tool_call, toolmessage_ack]`
- [x] 4.4 Return `WorkerResult(node_id=node_id, channel_updates={"messages": ...}, command=Command(goto=target))`
- [x] 4.5 If `handoff_to` is absent, preserve current behavior (write assistant response to `messages`)

## 5. handoff.py: message pairing, context_mode filter, summary

- [x] 5.1 Add `build_handoff_channel_updates(messages_snapshot, source_node_id, target_node_id, context_mode, tool_call_id, llm_tool_call_message) -> list[dict]`
- [x] 5.2 Implement `inherited` mode: pass `messages_snapshot` through unchanged, append the AIMessage (re-emit with original `tool_call_id`) and a synthetic ToolMessage
- [x] 5.3 Implement `isolated` mode: drop the snapshot, emit a `{"role": "system", "content": f"Handed off from {source_node_id}"}` message plus the AIMessage + ToolMessage pair
- [x] 5.4 Implement `summarized` mode: call a new `_summarize_messages(messages_snapshot, source_node_id) -> str` helper that uses the configured LLM (injected via port) to produce a structured summary; wrap as a system message, then append the AIMessage + ToolMessage pair
- [x] 5.5 Add tool_call_id collision handling: if the same `tool_call_id` appears twice in the snapshot, append `"-{uuid4_hex[:8]}"` to the second occurrence and log WARNING
- [x] 5.6 Add `filter_messages_for_handoff(messages, context_mode, source_node_id, target_node_id) -> list[dict]` as the public entry point; document the three modes

## 6. Tests: AgentExecutionPort

- [x] 6.1 Test handoff tool injected when `handoff_targets` is non-empty (assert tool list contains `handoff_to_agent` with correct `enum`)
- [x] 6.2 Test no handoff tool injected when `handoff_targets` is empty or absent
- [x] 6.3 Test handoff detection: mock LLM returns tool call to `handoff_to_agent`; assert port returns `handoff_to` in result dict
- [x] 6.4 Test invalid target rejected: mock LLM returns target not in valid list; assert error response returned (no `handoff_to`)
- [x] 6.5 Test per-target description included in tool schema when `descriptions_by_target` is provided
- [x] 6.6 Test fallback to generic description when `descriptions_by_target` is None

## 7. Tests: AgentWorker

- [x] 7.1 Test `handoff_to` in port result produces `WorkerResult(command=Command(goto=...))`
- [x] 7.2 Test `inherited` context_mode: resulting `messages` contains snapshot + AIMessage + ToolMessage
- [x] 7.3 Test `isolated` context_mode: resulting `messages` contains only system note + AIMessage + ToolMessage
- [x] 7.4 Test `summarized` context_mode: resulting `messages` contains only system summary + AIMessage + ToolMessage (mock the summarizer)
- [x] 7.5 Test AIMessage + ToolMessage pairing: `tool_call_id` is identical on both messages
- [x] 7.6 Test tool_call_id collision generates UUID suffix on second occurrence

## 8. Tests: PregelRuntime + end-to-end

- [x] 8.1 Test PregelRuntime populates `execution_context["handoff_targets"]` for an AGENT node with a static handoff edge
- [x] 8.2 Test PregelRuntime populates multiple targets for a `dynamic_handoff` edge with dict target
- [x] 8.3 Test PregelRuntime omits `handoff_targets` for non-AGENT nodes
- [x] 8.4 End-to-end: small graph `triage_agent --(dynamic_handoff)--> {billing, tech}`; mock LLM returns `handoff_to_agent(target="tech")`; assert PregelRuntime executes `tech` node in the next superstep

## 9. Tests: Compiler

- [x] 9.1 Test AGENT node with valid `handoff.context_mode` values compiles successfully
- [x] 9.2 Test AGENT node with invalid `handoff.context_mode` (e.g. `"secure"`) raises `GraphValidationError`
- [x] 9.3 Test AGENT node without `handoff` block compiles successfully (backward compat)

## 10. Documentation

- [x] 10.1 Update `docs/design/engine-design.md` with a "Multi-Agent Handoff" section covering execution path, three context_mode strategies, and tool-pairing contract
- [x] 10.2 Add a short example graph JSON to `docs/design/engine-design.md` showing static handoff and dynamic handoff side-by-side

## 11. Verification

- [x] 11.1 Run `ruff check src/hecate/ tests/` — expect 0 errors
- [x] 11.2 Run `ruff format --check src/ tests/` — expect all formatted
- [x] 11.3 Run `mypy src/` — expect 0 errors (existing false positives from optional deps excluded)
- [x] 11.4 Run `python -m pytest tests/test_engine/test_handoff.py tests/test_services/test_orchestration/test_agent_execution_port.py tests/test_engine/test_pregel.py -v` — all pass
- [x] 11.5 Run full test suite `python -m pytest tests/ -q` — no regressions
