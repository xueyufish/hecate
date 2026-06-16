## Context

Hecate has three agent execution modes (`chat`, `three_layer`, `workflow`) but only `workflow` mode uses the PregelRuntime graph engine in production. Chat mode bypasses the engine entirely, routing through ConversationService's imperative 700-line orchestration loop that handles context assembly, memory, knowledge retrieval, tool calling, streaming, and suggestions in a single monolithic method. Three_layer mode has a graph template (`build_three_layer_graph()` in `templates.py`) but only the test runner invokes it via PregelRuntime.

The engine layer is already unified — `templates.py` proves that three_layer is just a GraphConfig. The misalignment is in the services/api layer where chat.py directly calls ConversationService, which directly calls LLMService, bypassing the graph engine entirely.

The sub-services that ConversationService orchestrates are already well-decomposed: ContextAssembler, BudgetManager, WorkingMemoryService, UserMemoryService, CompressionPipeline, knowledge_base_service, llm_service, SuggestionService, EvidenceTracker. They don't need rewriting — they need to be called by Workers instead of by ConversationService.

## Goals / Non-Goals

**Goals:**
- All three agent modes (chat, three_layer, workflow) execute through PregelRuntime
- ConversationService's orchestration logic is replaced by graph edges and Workers
- Production-grade Workers replace `_TestWorker` for all node types
- Guardrail Hooks (PreLLMHook, PostLLMHook, PreToolHook, PostToolHook) are integrated into Workers as cross-cutting security — all modes get guard protection
- Token-level SSE streaming works through PregelRuntime's yield mechanism (`StreamMode.MESSAGES`)
- Existing sub-services (ContextAssembler, MemoryServices, LLMService, etc.) remain unchanged
- Chat API (`POST /v1/chat/completions`) behavior is preserved — same inputs, same outputs

**Non-Goals:**
- Conversational workflow mode (1.1.8 — that's the next change, built on top of this)
- StreamMode.DEBUG implementation (P2 but not this change)
- Database-backed CheckpointStore (P3)
- three_layer mode UI configuration (canvas-based three_layer editing)
- Performance optimization of PregelRuntime overhead for chat mode

## Decisions

### Decision 1: Worker granularity — one Worker per NodeType, not one per service

**Choice**: Each NodeType gets a dedicated Worker class that internally calls the appropriate services.

**Rationale**: ConversationService does 12 things internally (context assembly, memory loading, knowledge retrieval, LLM call, tool execution, evidence tracking, streaming, suggestions, opening remarks, fact extraction, provider shaping, compression). Making each a separate NodeType would require multi-node graphs even for simple chat, adding unnecessary complexity. Instead, each NodeType's Worker handles its domain internally.

Mapping:
- `CONVERSATION` → `_LLMWorker`: calls PreLLMHook, ContextAssembler, MemoryServices, LLMService, provider shaping, compression, PostLLMHook, evidence tracking internally
- `TOOL_CALL` → `_ToolWorker`: calls PreToolHook, tool registry, PostToolHook, evidence tracking
- `CONDITION` → `_ConditionWorker`: evaluates expressions against channel state
- `AGENT` → `_AgentWorker`: calls EnginePort.agent_execute for sub-agent delegation
- `KNOWLEDGE_RETRIEVAL` → `_KnowledgeWorker`: calls knowledge_base_service
- `VARIABLE_SET` → `_VariableSetWorker`: writes values to channels

**Alternative considered**: One Worker per service (12 Workers). Rejected because it would require complex multi-node graphs for simple chat mode.

### Decision 2: ConversationNode context loading strategy

**Choice**: The `_LLMWorker` loads context (memory, knowledge, compression) as pre-processing steps before the LLM call, not as separate graph nodes.

**Rationale**: For chat mode (single ConversationNode graph), context loading must happen inside the node. For workflow mode, users can add separate KnowledgeRetrieval nodes if they want. Both patterns work — the Worker's internal pre-processing is invisible to the graph topology. The key data flows through channels:
- Input: `messages` channel (user message injected as initial_input)
- Output: `messages` channel (assistant response appended)

**Alternative considered**: Make context assembly a separate graph node. Rejected because it would force chat mode to be a 5-node graph (context → memory → knowledge → LLM → suggestions), adding overhead and complexity for the simplest use case.

### Decision 3: Tool calling loop — cyclic graph edge, not Worker-internal loop

**Choice**: Tool calling is modeled as a cyclic edge pattern in the graph: `ConversationNode → ConditionNode (has_tool_call?) → ToolNode → ConversationNode`. This replaces ConversationService's `for iteration in range(max_iterations)` loop.

**Rationale**: This is how `build_three_layer_graph()` already works (planner → check_tools → tool_call → planner). Making it a graph pattern means:
- PregelRuntime's `max_supersteps` guards against infinite loops
- Checkpoints capture mid-loop state automatically
- The tool calling loop is visible in the graph topology (observable, debuggable)
- Interrupt/resume works mid-loop without special handling

**Alternative considered**: Keep tool loop inside `_LLMWorker` (imperative). Rejected because it duplicates ConversationService's approach and loses Pregel benefits (checkpoint, observability).

### Decision 4: Streaming architecture — Worker yields tokens, PregelRuntime passes through

**Choice**: `StreamMode.MESSAGES` makes PregelRuntime yield individual tokens from Workers. Workers that produce streaming output (LLMWorker) yield `{"type": "message", "content": token}` events. PregelRuntime collects and forwards these.

**Rationale**: Currently `StreamMode.UPDATES` yields per-node and `StreamMode.VALUES` yields full state. `StreamMode.MESSAGES` (already defined in types.py but not implemented) fills the gap for token-level streaming. The PregelRuntime's superstep loop already yields events — adding a third yield path is natural.

### Decision 5: Chat graph template structure

**Choice**: `build_chat_graph()` produces a 3-node graph:
```
[__start__] → [conversation] → [check_tools] ──(has tools)──→ [tool_call] → [conversation] (loop)
                                   │
                                   (no tools)
                                   ▼
                              [suggestions] → [__end__]
```

When `enable_suggestions=False` or `generate_opening=True`, the suggestion node is skipped via conditional edge.

**Rationale**: This mirrors ConversationService's actual behavior (LLM call → tool loop → suggestions) as a graph. The existing `build_three_layer_graph()` already uses this exact pattern (planner → check_tools → tool → loop).

### Decision 6: Suggestion and Opening Remarks as post-processing

**Choice**: Suggestions and opening remarks are handled by a `_SuggestionWorker` on a SUGGESTION NodeType, triggered via conditional edge after the conversation node. This replaces ConversationService's inline `_generate_followup_suggestions()` and `_generate_opening_remarks()` calls.

**Rationale**: Suggestions are optional and conditional. Making them a separate node with conditional routing is cleaner than baking them into the LLM Worker.

### Decision 7: WorkflowExecutionService as the unified entry point

**Choice**: A new `WorkflowExecutionService` class accepts an `AgentModel`, resolves the appropriate graph template, compiles it, creates the right Workers, injects Guardrail Hooks, and runs PregelRuntime. Both `chat.py` and workflow API call this service.

**Rationale**: Single entry point means single place to add logging, observability, rate limiting, and error handling. `chat.py` becomes a thin API adapter.

### Decision 8: Guard as Hook, not as graph node

**Choice**: Guard is a cross-cutting concern implemented via Guardrail Hooks (PreLLMHook, PostLLMHook, PreToolHook, PostToolHook) injected into Workers by WorkflowExecutionService. It is NOT a node in the graph. The guard node is removed from `build_three_layer_graph()`.

**Rationale**: Security should apply to ALL execution modes (chat, three_layer, workflow), not just three_layer. The existing guardrail.py already defines the perfect Hook interface:
- `PreLLMHook.on_pre_llm_call()` → called inside `_LLMWorker` before LLM invocation
- `PostLLMHook.on_post_llm_call()` → called inside `_LLMWorker` after LLM response
- `PreToolHook.on_pre_tool_call()` → called inside `_ToolWorker` before tool execution
- `PostToolHook.on_post_tool_call()` → called inside `_ToolWorker` after tool execution

Benefits of Hook over Node:
- Automatic: every LLM call and tool call goes through guardrails regardless of graph topology
- No graph pollution: graph templates stay focused on business logic, not security infrastructure
- Pluggable: different guardrail implementations per agent/workspace/tenant
- Currently ABC + NoOp defaults exist — we just need to wire them into Workers

**Alternative considered**: Keep guard as a CONVERSATION node in three_layer graph. Rejected because it means only three_layer has security, and every new graph template must remember to add guard nodes manually.

### Decision 9: Nested graph execution via _AgentWorker

**Choice**: `_AgentWorker` resolves the sub-agent by ID, packages parent channel context (messages, variables) as `initial_input`, and calls `WorkflowExecutionService.execute()` — enabling graph-within-graph execution. It does NOT call `EnginePort.agent_execute()` directly.

**Rationale**: This mirrors how Google ADK (LlmAgent composes sub_agents via WorkflowAgent) and IBM watsonx (Agent calls Agentic Workflow as Tool) handle composition. The engine layer supports arbitrary nesting because PregelRuntime is recursive — a Worker within one graph can invoke another graph execution. By routing through WorkflowExecutionService, the sub-agent gets the full template resolution, compilation, and guard hook injection pipeline, not just a raw LLM call.

This decision keeps the door open for P3's "Agent + Workflow composability" without implementing it now. When P3 introduces the Skill concept (Agent mounting Workflow as a skill), the engine layer won't need changes — only the service/API layer needs a new SkillRegistry that maps skills to Workers or nested graph executions.

**Alternative considered**: `_AgentWorker` calls `EnginePort.agent_execute()` which routes to the old ConversationService for chat-mode sub-agents. Rejected because this defeats the purpose of unified execution — a chat-mode sub-agent would bypass PregelRuntime entirely.

## Risks / Trade-offs

- **[Streaming regression]** → Current SSE streaming works perfectly through ConversationService's generators. Migrating to PregelRuntime's StreamMode.MESSAGES requires careful implementation and thorough regression testing. **Mitigation**: Implement StreamMode.MESSAGES first, test independently, then migrate chat.py.

- **[PregelRuntime overhead for simple chat]** → Going through graph compilation, channel registration, worker dispatch, checkpoint for a simple "one question one answer" adds overhead vs direct LLM call. **Mitigation**: Measure overhead; if >50ms, optimize by skipping checkpoint for non-interrupt graphs.

- **[Tool calling loop correctness]** → The cyclic edge pattern (conversation → check_tools → tool_call → conversation) must correctly terminate. PregelRuntime's `max_supersteps` guards this, but the ConditionWorker's expression evaluation must correctly detect tool_calls in the channel state. **Mitigation**: Comprehensive tests for tool calling loops with 1, 5, and max_iterations scenarios.

- **[Breaking API change surface]** → Although external API (`POST /v1/chat/completions`) stays the same, all internal service boundaries change. Any code that imports ConversationService directly will break. **Mitigation**: Search for all ConversationService imports before migration, update all call sites.

- **[Evidence tracking in graph context]** → EvidenceTracker currently receives `session_id` and `turn_index` from ConversationService. In the graph context, these need to come from channel state or PregelRuntime metadata. **Mitigation**: Pass session metadata via channel state, Workers read from channels.
