## ADDED Requirements

### Requirement: Chat graph template produces correct topology
The `build_chat_graph()` function SHALL return a GraphConfig that replicates ConversationService's orchestration as a graph: conversation node, optional tool-calling loop, and optional suggestion node.

#### Scenario: Basic chat without tools or suggestions
- **WHEN** `build_chat_graph(model="gpt-4o", system_prompt="You are helpful")` is called
- **THEN** the returned GraphConfig SHALL have: one CONVERSATION node ("llm"), one entry edge from "llm" to "check_tools", one CONDITION node ("check_tools"), edge "check_tools" → `{"true": "tool_call", "false": "__end__"}`

#### Scenario: Chat with suggestions enabled
- **WHEN** `build_chat_graph(model="gpt-4o", enable_suggestions=True)` is called
- **THEN** the returned GraphConfig SHALL route from "check_tools" (false branch) to a SUGGESTION node ("suggestions") before `__end__`

#### Scenario: Chat with tool calling
- **WHEN** `build_chat_graph(model="gpt-4o")` is called
- **THEN** the tool_call loop SHALL be: "llm" → "check_tools" → (true) → "tool_call" → "llm" (cycle)

### Requirement: Chat graph state channels
The `build_chat_graph()` SHALL define channels: `messages` (TOPIC), `_has_tool_call` (LAST_VALUE), `_route` (LAST_VALUE), and session metadata channels.

#### Scenario: Channel definitions
- **WHEN** the graph is compiled
- **THEN** channels SHALL include: `messages` (TOPIC, default=[]), `_has_tool_call` (LAST_VALUE), `_route` (LAST_VALUE), `_session_id` (LAST_VALUE), `_agent_id` (LAST_VALUE), `_user_id` (LAST_VALUE), `_turn_index` (LAST_VALUE)
