## ADDED Requirements

### Requirement: LLM Worker handles full conversation pre-processing
The `_LLMWorker` SHALL be a production Worker for CONVERSATION nodes that performs context assembly, memory loading, compression, knowledge retrieval, provider shaping, and LLM invocation in sequence. It SHALL call existing services (ContextAssembler, WorkingMemoryService, UserMemoryService, CompressionPipeline, knowledge_base_service, llm_service) through EnginePort.

#### Scenario: Simple chat without tools
- **WHEN** a CONVERSATION node executes with `{"model": "gpt-4o", "system_prompt": "You are helpful"}` config and `messages` channel contains user message
- **THEN** the worker SHALL assemble context, invoke LLM, and return `{"messages": [{"role": "assistant", "content": "response"}]}` in channel_updates

#### Scenario: Chat with tool calls returned
- **WHEN** the LLM response contains tool_calls
- **THEN** the worker SHALL return `{"messages": [assistant_message_with_tool_calls], "_has_tool_call": true}` in channel_updates
- **AND** the `_has_tool_call` value SHALL be used by a downstream ConditionNode to route to the ToolWorker

#### Scenario: Streaming token output
- **WHEN** StreamMode is MESSAGES
- **THEN** the worker SHALL yield individual tokens from llm_service.chat_stream() before returning the final WorkerResult

#### Scenario: Context assembly with memory
- **WHEN** agent_id and user_id are provided in node config
- **THEN** the worker SHALL load L1 memory blocks (WorkingMemoryService), L3 user memories (UserMemoryService), compress history (CompressionPipeline), and assemble context (ContextAssembler) before LLM invocation

#### Scenario: Knowledge retrieval integration
- **WHEN** kb_ids are provided in node config
- **THEN** the worker SHALL call knowledge_base_service.search() for each KB and inject results into context assembly

### Requirement: Tool Worker executes tools with evidence tracking
The `_ToolWorker` SHALL parse tool calls from channel state, execute them, capture evidence, and inject results back into messages.

#### Scenario: Execute tool call
- **WHEN** a TOOL_CALL node executes and `messages` channel contains an assistant message with tool_calls
- **THEN** the worker SHALL parse tool calls, execute each tool, and return `{"messages": [tool_result_messages]}` in channel_updates

#### Scenario: Tool execution error
- **WHEN** a tool execution raises an exception
- **THEN** the worker SHALL return the error as a tool result message with `is_error: true`, not raise the exception

#### Scenario: Evidence capture
- **WHEN** an EvidenceTracker is available via EnginePort
- **THEN** the worker SHALL capture tool name, arguments, result, session_id, and turn_index for each tool execution

### Requirement: Knowledge Worker retrieves documents
The `_KnowledgeWorker` SHALL query knowledge bases and write retrieved chunks to channels.

#### Scenario: Knowledge retrieval
- **WHEN** a KNOWLEDGE_RETRIEVAL node executes with `{"kb_ids": ["uuid1"], "query_template": "{messages[-1].content}", "top_k": 5}`
- **THEN** the worker SHALL extract query from messages, search specified KBs, and return `{"context": "retrieved content", "messages": [{"role": "system", "content": "Retrieved N docs"}]}` in channel_updates

### Requirement: Condition Worker evaluates expressions
The `_ConditionWorker` SHALL evaluate expressions against channel state and write the result to the `_route` channel.

#### Scenario: Tool call detection
- **WHEN** a CONDITION node with expression `has_tool_call` evaluates against channel state where `_has_tool_call` is `true`
- **THEN** the worker SHALL return `{"_route": "true"}` in channel_updates

#### Scenario: Expression evaluation
- **WHEN** a CONDITION node with expression `category == 'finance'` evaluates against channel state where `category` is `"finance"`
- **THEN** the worker SHALL return `{"_route": "true"}` in channel_updates

### Requirement: Agent Worker delegates to sub-agent
The `_AgentWorker` SHALL call EnginePort.agent_execute() with the agent_id from node config.

#### Scenario: Sub-agent invocation
- **WHEN** an AGENT node executes with `{"agent_id": "uuid", "invocation_mode": "direct"}`
- **THEN** the worker SHALL call `EnginePort.agent_execute(agent_id, messages)` and return the sub-agent's response in channel_updates

### Requirement: Suggestion Worker generates follow-up questions
The `_SuggestionWorker` SHALL call SuggestionService to generate opening remarks or follow-up suggestions based on node config.

#### Scenario: Follow-up suggestions
- **WHEN** a SUGGESTION node executes after a conversation turn with `{"agent_persona": "...", "enable_suggestions": true}`
- **THEN** the worker SHALL return `{"suggested_questions": ["q1", "q2", "q3"]}` in channel_updates

#### Scenario: Opening remarks
- **WHEN** a SUGGESTION node executes with `{"generate_opening": true}` and it is the first turn
- **THEN** the worker SHALL return both `{"content": "opening text", "suggested_questions": ["q1", "q2"]}` in channel_updates

### Requirement: Variable Set Worker writes values to channels
The `_VariableSetWorker` SHALL write configured variable values to channels.

#### Scenario: Variable assignment
- **WHEN** a VARIABLE_SET node executes with `{"variable_name": "status", "value": "completed"}`
- **THEN** the worker SHALL return `{"status": "completed"}` in channel_updates
