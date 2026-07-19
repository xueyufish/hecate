## Why

Hecate's `EnginePort.agent_execute()` exists as an abstract interface with a concrete implementation in `AgentExecutionPort`, but the implementation is a thin shell: it loads the agent's persona and skills, then calls `llm_service.chat(tools=None)` directly. This bypasses the full LLM pipeline — no tool loading, no knowledge base retrieval, no guard hooks (PreLLM/PostLLM), no context assembly, no token budget management. Agents invoked via `agent_execute` get a degraded experience compared to CONVERSATION node execution through `LLMWorker`. Additionally, the Agent-as-Tool capability (`AgentDefinition` + `AgentTool`) is fully built at the engine layer but lacks a DSL-level `invocation_mode` switch to activate it from graph definitions.

## What Changes

- **Upgrade `AgentExecutionPort.agent_execute()`** — load agent's configured tools, query knowledge bases, wire PreLLMHook/PostLLMHook, call `context_assemble`, and pass tools to LLM. Bring agent execution to parity with LLMWorker's pipeline.
- **Add `invocation_mode` field to AGENT node DSL schema** — support `"graph"` (default, nested execution via WorkflowExecutionService) and `"tool"` (expose agent as callable tool via AgentDefinition) modes.
- **Wire `invocation_mode` in AgentWorker** — read the field from node config and route to either nested graph execution or Agent-as-Tool registration.
- **Integrate `AgentDefinition.resolve_tools()` in agent_execute** — apply whitelist/blacklist filtering when an AgentDefinition is provided.
- **Add AgentExecutionPort tests** — currently zero test coverage for the concrete agent execution adapter.

## Capabilities

### New Capabilities

_(none — all capabilities reference existing specs)_

### Modified Capabilities

- `agent-invocation`: Upgraded agent_execute pipeline (tools, KB, hooks, context assembly), new invocation_mode field in AGENT node DSL, tool filtering integration with AgentDefinition.

## Impact

- **Modified files**:
  - `src/hecate/services/orchestration/agent_execution_port.py` — rewrite agent_execute to use full pipeline
  - `src/hecate/engine/graph-dsl.schema.json` — add invocation_mode to AGENT node config
  - `src/hecate/engine/workers/agent_worker.py` — read invocation_mode, route to AgentTool when mode=tool
  - `src/hecate/engine/compiler.py` — validate invocation_mode during compilation
- **New files**:
  - `tests/test_services/test_orchestration/test_agent_execution_port.py` — AgentExecutionPort unit tests
- **No breaking changes**: invocation_mode defaults to "graph" (existing behavior). agent_execute upgrade is additive — existing callers get richer behavior automatically.
- **No new dependencies**: Uses existing LLMService, KnowledgeBaseService, GuardrailHooks, ContextEngine.
