## ADDED Requirements — 新增需求

### Requirement: LLM Call node type — LLM 调用节点类型
The system SHALL provide a `conversation` node type with a configuration panel containing: model selector (dropdown from /v1/models), system prompt (textarea), temperature (slider 0-2), max_tokens (number input).

系统应提供 `conversation` 节点类型，其配置面板包含：模型选择器（从 /v1/models 下拉选择）、系统提示词（文本域）、temperature（滑块 0-2）、max_tokens（数字输入框）。

#### Scenario: Configure LLM node with model and prompt — 配置 LLM 节点的模型和提示词
- **WHEN** user clicks on an LLM Call node
- **THEN** a side panel opens showing model dropdown, system prompt textarea, temperature slider, and max_tokens input pre-filled with the node's current config
- **当**用户点击 LLM Call 节点
- **则**侧边面板打开，显示模型下拉框、系统提示词文本域、temperature 滑块和 max_tokens 输入框，并预填节点当前配置

#### Scenario: Save LLM node configuration — 保存 LLM 节点配置
- **WHEN** user modifies the model selector and system prompt in the side panel
- **THEN** the node's config is updated and the node label changes to show the selected model name
- **当**用户在侧边面板修改模型选择器和系统提示词
- **则**节点配置被更新，节点标签更改为显示所选模型名称

### Requirement: Condition node type — 条件节点类型
The system SHALL provide a `condition` node type with a configuration panel containing an expression field. Condition nodes SHALL have multiple output handles (one per branch).

系统应提供 `condition` 节点类型，其配置面板包含一个表达式字段。条件节点应有多个输出句柄（每个分支一个）。

#### Scenario: Configure condition with two branches — 配置带有两个分支的条件节点
- **WHEN** user configures a condition node with expression and connects two edges labeled "true" and "false"
- **THEN** the graph DSL stores the condition config and the edge targets as a dict `{"true": "node-a", "false": "node-b"}`
- **当**用户配置带有表达式的条件节点，并连接两条标记为"true"和"false"的边
- **则**图 DSL 将条件配置和边目标存储为字典 `{"true": "node-a", "false": "node-b"}`

### Requirement: Tool Call node type — 工具调用节点类型
The system SHALL provide a `tool-call` node type with a configuration panel containing a tool selector dropdown populated from GET /api/tools.

系统应提供 `tool-call` 节点类型，其配置面板包含一个从 GET /api/tools 获取数据的工具选择器下拉框。

#### Scenario: Configure tool call node — 配置工具调用节点
- **WHEN** user selects a tool from the dropdown
- **THEN** the node config is updated with `tool_name` and the node label shows the selected tool name
- **当**用户从下拉框中选择一个工具
- **则**节点配置更新为 `tool_name`，节点标签显示所选工具名称

### Requirement: Sub-Agent node type — 子 Agent 节点类型
The system SHALL provide an `agent` node type with a configuration panel containing an agent selector dropdown populated from GET /api/agents.

系统应提供 `agent` 节点类型，其配置面板包含一个从 GET /api/agents 获取数据的 agent 选择器下拉框。

#### Scenario: Configure sub-agent node — 配置子 agent 节点
- **WHEN** user selects an agent from the dropdown
- **THEN** the node config is updated with `agent_ref` set to the agent's ID
- **当**用户从下拉框中选择一个 agent
- **则**节点配置更新为 `agent_ref` 设置为该 agent 的 ID

### Requirement: Knowledge Retrieval node type — 知识检索节点类型
The system SHALL provide a `knowledge-retrieval` node type with a configuration panel containing a knowledge base selector dropdown populated from GET /api/knowledge-bases and a query template textarea.

系统应提供 `knowledge-retrieval` 节点类型，其配置面板包含一个从 GET /api/knowledge-bases 获取数据的知识库选择器下拉框和一个查询模板文本域。

#### Scenario: Configure knowledge retrieval node — 配置知识检索节点
- **WHEN** user selects a knowledge base and enters a query template
- **THEN** the node config stores `knowledge_base_id` and `query_template` and the node label shows the knowledge base name
- **当**用户选择知识库并输入查询模板
- **则**节点配置存储 `knowledge_base_id` 和 `query_template`，节点标签显示知识库名称

### Requirement: Variable Set node type — 变量设置节点类型
The system SHALL provide a `variable-set` node type with a configuration panel containing channel name input and value expression textarea for writing to graph state channels.

系统应提供 `variable-set` 节点类型，其配置面板包含通道名称输入和值表达式文本域，用于写入图状态通道。

#### Scenario: Configure variable set node — 配置变量设置节点
- **WHEN** user enters a channel name and value expression
- **THEN** the node config stores the channel writable mapping and the node label shows the channel name
- **当**用户输入通道名称和值表达式
- **则**节点配置存储通道可写映射，节点标签显示通道名称
