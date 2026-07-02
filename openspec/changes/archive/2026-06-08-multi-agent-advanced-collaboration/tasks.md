## 1. EventBus Core (2.3a)

- [x] 1.1 Create `engine/eventbus.py` with `CollaborationEventType` enum (AGENT_MESSAGE, AGENT_REQUEST, AGENT_RESPONSE, TASK_ASSIGNED, TASK_COMPLETED, NEGOTIATION_PROPOSAL, NEGOTIATION_ACCEPT, NEGOTIATION_REJECT, DEBATE_ARGUMENT, DEBATE_REBUTTAL, DEBATE_CONCLUSION)
- [x] 1.2 Add `CollaborationEvent` frozen dataclass (id, topic, sender, event_type, payload, timestamp) to `engine/eventbus.py`
- [x] 1.3 Define `EventBus` ABC with abstract methods: `publish(topic, event)`, `subscribe(topic, handler)`, `unsubscribe(topic, handler)`, `close()`
- [x] 1.4 Implement `InMemoryEventBus` using `asyncio.Queue` per topic with topic isolation, multiple subscriber support, and close-with-flush
- [x] 1.5 Add `event_bus: EventBus | None = None` parameter to `PregelRuntime.__init__()` and pass it through `execution_context`

## 2. EventBus Tests

- [x] 2.1 Test `CollaborationEvent` creation, immutability, auto-generated fields
- [x] 2.2 Test `CollaborationEventType` enum values
- [x] 2.3 Test `InMemoryEventBus` publish/subscribe/unsubscribe, topic isolation, multiple subscribers, close flush, empty topic
- [x] 2.4 Test `EventBus` ABC is not instantiable
- [x] 2.5 Test PregelRuntime passes event_bus in execution_context when configured, omits it when not

## 3. Negotiation Templates (2.3b)

- [x] 3.1 Implement `build_negotiation_graph(proposer_model, responder_model, proposer_prompt, responder_prompt, max_rounds)` returning `GraphConfig` with proposer AGENT node, responder AGENT node, check_agreement CONDITION node, negotiation loop edges, and `agreement_status` LAST_VALUE channel
- [x] 3.2 Implement `build_debate_graph(debater_a_model, debater_b_model, judge_model, rounds)` returning `GraphConfig` with alternating debate turns, round counter, optional judge evaluation node

## 4. Negotiation Template Tests

- [x] 4.1 Test `build_negotiation_graph` produces valid `GraphConfig` with correct nodes, edges, channels, and entry point
- [x] 4.2 Test `build_negotiation_graph` output compiles successfully via `GraphCompiler.compile()`
- [x] 4.3 Test `build_debate_graph` produces valid `GraphConfig` with and without judge
- [x] 4.4 Test `build_debate_graph` output compiles successfully via `GraphCompiler.compile()`
- [x] 4.5 Test factory functions importable from `engine.templates`

## 5. TaskAllocator (2.3c)

- [x] 5.1 Create `engine/task_allocator.py` with `TaskAllocator` ABC defining `allocate(task, candidates, create_if_not_found=False)` returning `AgentModel | None`
- [x] 5.2 Implement `SemanticTaskAllocator` that calls `port.llm_invoke()` with a ranking prompt, parses the LLM response for the best match, and returns the top candidate; on LLM failure logs error and returns None
- [x] 5.3 Implement `RoundRobinTaskAllocator` cycling through candidates in order; returns None for empty candidates
- [x] 5.4 `create_if_not_found=True` SHALL raise `NotImplementedError` with P3 reservation message

## 6. TaskAllocator Tests

- [x] 6.1 Test `TaskAllocator` ABC is not instantiable
- [x] 6.2 Test `SemanticTaskAllocator` calls `port.llm_invoke()` and returns best-match agent
- [x] 6.3 Test `SemanticTaskAllocator` returns None when no suitable candidate
- [x] 6.4 Test `SemanticTaskAllocator` returns None (not raises) on LLM failure
- [x] 6.5 Test `RoundRobinTaskAllocator` cycles through candidates in order
- [x] 6.6 Test `RoundRobinTaskAllocator` returns None for empty candidates
- [x] 6.7 Test `create_if_not_found=True` raises `NotImplementedError`

## 7. Agent-as-Tool (2.3d)

- [x] 7.1 Create `engine/agent_tool.py` with `AgentDefinition` dataclass (agent_id, description, prompt_override, tools, disallowed_tools=["agent_execute"], skills, model_override, context_mode="inherited", max_turns, timeout_seconds)
- [x] 7.2 Implement `AgentTool` class with `name`, `description`, and `execute(args)` method that calls `port.agent_execute()` with the AgentDefinition's overrides
- [x] 7.3 Implement tool whitelist/blacklist resolution: if tools is not None → whitelist minus disallowed; if tools is None → inherit all minus disallowed
- [x] 7.4 Implement `context_mode="isolated"` — AgentTool creates fresh message context with only the task; `"inherited"` — passes parent's messages channel
- [x] 7.5 Implement `timeout_seconds` enforcement via `asyncio.wait_for`
- [x] 7.6 Implement `max_turns` enforcement via context parameter to `agent_execute()`

## 8. Agent-as-Tool Tests

- [x] 8.1 Test `AgentDefinition` minimal creation (defaults: tools=None, disallowed_tools=["agent_execute"], context_mode="inherited")
- [x] 8.2 Test `AgentDefinition` full creation with all fields
- [x] 8.3 Test `AgentTool` name and description derivation from AgentDefinition
- [x] 8.4 Test `AgentTool.execute()` calls `port.agent_execute()` with correct arguments
- [x] 8.5 Test whitelist minus blacklist resolution (tools=["a","b","agent_execute"], disallowed=["agent_execute"] → ["a","b"])
- [x] 8.6 Test inherit minus blacklist resolution (tools=None, parent tools=["a","b","agent_execute"], disallowed=["agent_execute"] → ["a","b"])
- [x] 8.7 Test empty whitelist (tools=[] → no tools)
- [x] 8.8 Test context_mode="isolated" sends only task message
- [x] 8.9 Test context_mode="inherited" sends parent messages
- [x] 8.10 Test timeout_seconds enforcement (TimeoutError on exceed)
- [x] 8.11 Test max_turns enforcement

## 9. Integration: EnginePort Extension

- [x] 9.1 Add `agent_definition: AgentDefinition | None = None` parameter to `EnginePort.agent_execute()` default implementation, raising `NotImplementedError` regardless
- [x] 9.2 Update `AgentWorker` to pass `agent_definition` from node config when `invocation_mode="tool"` is set

## 10. Integration Tests

- [x] 10.1 Test EventBus integration with PregelRuntime: agents communicate via pub/sub during graph execution
- [x] 10.2 Test negotiation graph executes proposal → response → accept/reject loop successfully
- [x] 10.3 Test debate graph executes alternating arguments with optional judge verdict
- [x] 10.4 Test TaskAllocator integrates with agent selection in a multi-agent graph
- [x] 10.5 Test AgentTool invocation from within an LLM tool-calling loop

## 11. Verification

- [x] 11.1 Run `ruff check src/hecate/ tests/` — 0 errors
- [x] 11.2 Run `ruff format --check src/ tests/` — 0 errors
- [x] 11.3 Run `mypy src/` — 0 errors
- [x] 11.4 Run `python -m pytest tests/ -q` — all pass
