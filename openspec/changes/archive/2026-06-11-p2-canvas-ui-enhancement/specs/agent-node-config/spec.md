## ADDED Requirements — 新增需求

### Requirement: Agent node config panel with structured form — Agent 节点配置面板（结构化表单）
Agent 节点配置面板应显示一个结构化表单，包含以下字段：agent 选择器（下拉列表）、角色描述（文本域）、调用模式（单选）、通道选择器（双列列表）和模型覆盖（文本输入）。

#### Scenario: Agent node config panel renders all fields — 配置面板渲染所有字段
- **当** 用户点击画布上的 agent 节点
- **则** 配置面板显示：agent 选择器下拉列表、角色描述文本域、调用模式单选（direct/tool）、包含可读/可写列表的通道选择器，以及模型覆盖文本输入

#### Scenario: Agent selector populates from API — 从 API 填充 Agent 选择器
- **当** 用户打开 agent 节点配置面板
- **则** agent 选择器下拉列表应从 `/api/agents` 获取可用 agent 并按名称显示

#### Scenario: Agent selector sets agent_ref — Agent 选择器设置 agent_ref
- **当** 用户从下拉列表中选择一个 agent
- **则** 节点的 `config.agent_ref` 应设置为所选 agent 的 ID

### Requirement: Role description field maps to system_prompt — 角色描述字段映射到 system_prompt
Agent 节点配置面板应包含一个角色描述文本域，映射到节点的 `config.system_prompt` 字段。

#### Scenario: Role description updates system_prompt — 角色描述更新 system_prompt
- **当** 用户在角色描述字段中输入 "You are a research analyst"
- **则** 节点的 `config.system_prompt` 应更新为 "You are a research analyst"

### Requirement: Invocation mode selector — 调用模式选择器
Agent 节点配置面板应包含一个调用模式单选选择器，选项为 "Direct" 和 "Tool"，分别映射到 `config.invocation_mode` 的值 "direct" 和 "tool"。

#### Scenario: Select direct invocation mode — 选择直接调用模式
- **当** 用户在调用模式单选中选择 "Direct"
- **则** 节点的 `config.invocation_mode` 应设置为 "direct"

#### Scenario: Select tool invocation mode — 选择工具调用模式
- **当** 用户在调用模式单选中选择 "Tool"
- **则** 节点的 `config.invocation_mode` 应设置为 "tool"

### Requirement: Channel selector with readable/writable dual-list — 带可读/可写双列列表的通道选择器
Agent 节点配置面板应包含一个通道选择器组件，该组件从图谱的 `state` 声明中读取可用通道，并允许用户为 agent 分配可读、可写、两者或都不分配的通道。

#### Scenario: Channel selector shows graph state channels — 通道选择器显示图谱状态通道
- **当** 用户打开 agent 节点的通道选择器，该节点所在图谱包含状态通道 "messages"、"research_data" 和 "analysis_results"
- **则** 选择器应将所有三个通道显示为选项

#### Scenario: Assign readable channels — 分配可读通道
- **当** 用户将 "messages" 和 "research_data" 移动到可读列表
- **则** 节点的 `config.channels.readable` 应设置为 ["messages", "research_data"]

#### Scenario: Assign writable channels — 分配可写通道
- **当** 用户将 "messages" 和 "analysis_results" 移动到可写列表
- **则** 节点的 `config.channels.writable` 应设置为 ["messages", "analysis_results"]

#### Scenario: Empty graph shows channel hint — 空图谱显示通道提示
- **当** 图谱未声明任何状态通道
- **则** 通道选择器应显示提示"在图谱设置中添加通道"，并允许自由输入通道名称

### Requirement: Model override field — 模型覆盖字段
Agent 节点配置面板应包含一个模型覆盖文本输入，用于设置 agent 节点的 `config.model`。

#### Scenario: Model override sets config.model — 模型覆盖设置 config.model
- **当** 用户在模型覆盖字段中输入 "gpt-4o-mini"
- **则** 节点的 `config.model` 应设置为 "gpt-4o-mini"

#### Scenario: Empty model override removes override — 清空模型覆盖移除覆盖
- **当** 用户清空模型覆盖字段
- **则** 应从配置中移除节点的 `config.model`
