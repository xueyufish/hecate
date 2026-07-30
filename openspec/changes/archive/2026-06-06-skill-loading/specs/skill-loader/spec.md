## ADDED Requirements — 新增需求

### Requirement: SkillLoader 将 agent 技能解析为格式化指令 — SkillLoader 将 agent 技能解析为格式化指令
系统 SHALL 在 `services/skill/loader.py` 中提供一个 `SkillLoader` 服务，接受 agent ID 和工作空间 ID，查询 agent 的 `skills` 列表，在工作空间内按名称加载匹配的 `SkillModel` 记录，并返回用于系统提示词注入的格式化 XML 字符串。

#### Scenario: 带技能的 agent 加载所有指令
- **WHEN** 调用 `SkillLoader.format_skills(agent_id, workspace_id)` 且 agent 的 `skills=["code-review", "unit-test"]`
- **THEN** 加载器 SHALL 按名称和工作空间查询 `SkillModel`，将每个格式化为 `<skill name="...">description\n\ninstructions</skill>`，包裹在 `<skills>` 标签中，并返回 XML 块

#### Scenario: 无技能的 agent 返回空字符串
- **WHEN** 为 `skills=[]` 的 agent 调用 `format_skills()`
- **THEN** 加载器 SHALL 返回空字符串

#### Scenario: 在工作空间中未找到技能名称
- **WHEN** agent 引用了技能名称 "missing-skill"，但工作空间中不存在具有该名称的 `SkillModel`
- **THEN** 加载器 SHALL 记录警告并跳过该技能，继续处理剩余技能

#### Scenario: auto_load=True 的技能始终包含在内
- **WHEN** 一个技能设置了 `auto_load=True`
- **THEN** 它 SHALL 始终包含在格式化输出中，无论 agent 是否在其 `skills` 字段中显式列出它

### Requirement: SkillLoader 遵守每个技能的 token 预算 — SkillLoader 遵守每个技能的 token 预算
加载器 SHALL 在格式化之前将单个技能指令截断到其 `max_tokens` 限制。如果总格式化技能块超过可配置的预算，加载器 SHALL 丢弃技能（从最低优先级开始）直到满足预算。

#### Scenario: 技能超过 max_tokens
- **WHEN** 一个技能设置了 `max_tokens=500` 但其指令会产生约 2000 token
- **THEN** 加载器 SHALL 将指令截断到大约 500 token（在句子或段落边界处分割）

#### Scenario: 总技能超过预算
- **WHEN** 组合的格式化技能超过系统预算（默认 4000 token）
- **THEN** 加载器 SHALL 首先丢弃 `auto_load=False` 的技能，然后截断剩余技能以适应

### Requirement: 技能以 XML 块形式注入系统提示词 — 技能以 XML 块形式注入系统提示词
当为 agent 加载技能时，格式化后的 XML 块 SHALL 附加到 agent 的 persona（系统提示词）之后，然后再调用 LLM。

#### Scenario: 带 persona 和技能的聊天模式 agent
- **WHEN** 使用 `agent_id` 调用 `WorkflowExecutionService.execute()` 且 agent 有 `persona="You are a coding assistant"` 和 `skills=["code-review"]`
- **THEN** 传递给 `build_chat_graph()` 的系统提示词 SHALL 为 `"You are a coding assistant\n\n<skills>\n<skill name=\"code-review\">\n...\n</skill>\n</skills>"`

#### Scenario: 带技能的子 agent 执行
- **WHEN** 为带技能的 agent 调用 `AgentExecutionPort.agent_execute()`
- **THEN** 系统消息 SHALL 包含 agent 的 persona，后跟格式化的技能 XML 块

#### Scenario: persona=None 且带技能的 agent
- **WHEN** agent 的 `persona=None` 且 `skills=["code-review"]`
- **THEN** 系统提示词 SHALL 为 `"You are a helpful assistant.\n\n<skills>\n<skill name=\"code-review\">\n...\n</skill>\n</skills>"`
