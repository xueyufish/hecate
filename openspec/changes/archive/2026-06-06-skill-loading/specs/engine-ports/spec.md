## MODIFIED Requirements — 修改的需求

### Requirement: 工具执行通过 ToolRegistry 路由 — 工具执行通过 ToolRegistry 路由
- **WHEN** 调用 `tool_execute(name, args, context)`
- **THEN** 它 SHALL 通过 ToolRegistry 路由调用，ToolRegistry 按名称和源类型解析工具，通过相应的执行器执行，并返回工具结果

#### Scenario: 通过 registry 执行工具
- **WHEN** 调用 `tool_execute("web_search", {"query": "test"}, context)`
- **THEN** 适配器 SHALL 委托给 `ToolRegistry.execute("web_search", {"query": "test"}, context)` 并返回 registry 的结果

#### Scenario: 工具未找到
- **WHEN** 调用 `tool_execute("nonexistent", args, context)` 且工具不存在
- **THEN** 它 SHALL 引发 `ValueError`，消息指示未找到工具

### Requirement: Agent 执行为系统提示词加载技能 — Agent 执行为系统提示词加载技能
当为子 agent 调用 `agent_execute()` 时，系统 SHALL 通过 `SkillLoader` 加载 agent 的技能，并将格式化后的 XML 块注入到系统消息中，放在 agent 的 persona 旁边。

#### Scenario: 带 persona 和技能的子 agent
- **WHEN** 为一个 `persona="Expert coder"` 且 `skills=["code-review"]` 的 agent 调用 `agent_execute(agent_id, messages, channel_snapshot)`
- **THEN** 系统消息 SHALL 为 `"Expert coder\n\n<skills>\n<skill name=\"code-review\">\n...\n</skill>\n</skills>"`，后跟会话消息

#### Scenario: 不带技能的子 agent
- **WHEN** 为一个 `skills=[]` 的 agent 调用 `agent_execute()`
- **THEN** 系统消息 SHALL 仅为 agent 的 persona，与当前行为相同
