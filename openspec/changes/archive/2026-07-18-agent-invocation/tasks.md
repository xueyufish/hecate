## 1. AgentExecutionPort Upgrade

- [x] 1.1 Add `pre_hook`, `post_hook`, `context_engine` optional parameters to `AgentExecutionPort.__init__()` with NoOp defaults
- [x] 1.2 Load agent's configured tools from AgentModel within `agent_execute()` (query ToolModel by agent's tool_ids)
- [x] 1.3 Query agent's knowledge bases via `self.knowledge_query()` and inject results as context messages
- [x] 1.4 Apply `AgentDefinition.resolve_tools()` filtering when agent_definition is provided (whitelist/blacklist)
- [x] 1.5 Wire PreLLMHook and PostLLMHook around LLM invocation in `agent_execute()`
- [x] 1.6 Call `self.context_assemble()` before LLM invocation to apply context engineering pipeline
- [x] 1.7 Pass assembled tools to `llm_service.chat()` (currently hardcoded `tools=None`)

## 2. DSL Schema & Compiler

- [x] 2.1 Add `invocation_mode` field (enum: "graph", "tool", default "graph") to AGENT node definition in `graph-dsl.schema.json`
- [x] 2.2 Validate `invocation_mode` value in `GraphCompiler` — reject invalid values with clear error

## 3. AgentWorker Routing

- [x] 3.1 Read `invocation_mode` from node_config in `AgentWorker.execute()`
- [x] 3.2 When `invocation_mode == "tool"`, create `AgentTool` from `agent_definition` in config and register in parent tool list instead of executing

## 4. Factory Wiring

- [x] 4.1 Update `_ProductionEnginePort.__init__()` to accept and pass guard hooks to AgentExecutionPort
- [x] 4.2 Update `create_engine_port()` factory to accept `pre_hook`, `post_hook`, `context_engine` parameters

## 5. Tests

- [x] 5.1 Test AgentExecutionPort loads tools and passes them to LLM (mock LLMService)
- [x] 5.2 Test AgentExecutionPort queries knowledge bases and injects context
- [x] 5.3 Test AgentExecutionPort applies PreLLMHook BLOCK (returns blocked response, no LLM call)
- [x] 5.4 Test AgentDefinition.resolve_tools() filtering — whitelist narrows tools, blacklist removes tools
- [x] 5.5 Test AgentWorker routes to AgentTool when invocation_mode="tool"
- [x] 5.6 Test AgentWorker defaults to graph mode when invocation_mode is missing

## 6. Verification

- [x] 6.1 Run `ruff check src/hecate/ tests/ && ruff format --check src/ tests/` — 0 errors
- [x] 6.2 Run `mypy src/` — 0 errors
- [x] 6.3 Run `python -m pytest tests/test_services/test_orchestration/ -q` — all pass
