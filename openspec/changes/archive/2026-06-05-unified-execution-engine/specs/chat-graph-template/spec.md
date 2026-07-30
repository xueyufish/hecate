## ADDED Requirements — 新增需求

### Requirement：聊天图模板生成正确的拓扑 — Chat graph template produces correct topology
`build_chat_graph()` 函数应返回一个 GraphConfig，将 ConversationService 的编排复制为图：会话节点、可选的工具调用循环和可选的建议节点。

#### Scenario：没有工具或建议的基本聊天 — Basic chat without tools or suggestions
- **当** `build_chat_graph(model="gpt-4o", system_prompt="You are helpful")` 被调用时
- **则** 返回的 GraphConfig 应具有：一个 CONVERSATION 节点（"llm"）、一条从 "llm" 到 "check_tools" 的入口边、一个 CONDITION 节点（"check_tools"）、边 "check_tools" → `{"true": "tool_call", "false": "__end__"}`

#### Scenario：启用建议的聊天 — Chat with suggestions enabled
- **当** `build_chat_graph(model="gpt-4o", enable_suggestions=True)` 被调用时
- **则** 返回的 GraphConfig 应从 "check_tools"（false 分支）路由到 `__end__` 之前的 SUGGESTION 节点（"suggestions"）

#### Scenario：带工具调用的聊天 — Chat with tool calling
- **当** `build_chat_graph(model="gpt-4o")` 被调用时
- **则** tool_call 循环应为："llm" → "check_tools" → (true) → "tool_call" → "llm"（循环）

### Requirement：聊天图状态通道 — Chat graph state channels
`build_chat_graph()` 应定义通道：`messages`（TOPIC）、`_has_tool_call`（LAST_VALUE）、`_route`（LAST_VALUE）以及会话元数据通道。

#### Scenario：通道定义 — Channel definitions
- **当** 图被编译时
- **则** 通道应包括：`messages`（TOPIC，默认=[]）、`_has_tool_call`（LAST_VALUE）、`_route`（LAST_VALUE）、`_session_id`（LAST_VALUE）、`_agent_id`（LAST_VALUE）、`_user_id`（LAST_VALUE）、`_turn_index`（LAST_VALUE）
