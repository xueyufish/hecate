## ADDED Requirements — 新增需求

### Requirement: Template customization mode after loading — 加载后的模板自定义模式
加载编排模板后，系统应启用模板自定义模式，允许用户编辑 agent 角色、添加/删除节点、调整连接和修改通道声明。

#### Scenario: Load template enters customization mode — 加载模板进入自定义模式
- **当** 用户从模板选择器加载模板
- **则** 画布进入自定义模式，所有编辑能力均已启用

#### Scenario: Customization mode visual indicator — 自定义模式视觉指示器
- **当** 画布处于自定义模式
- **则** 工具栏应显示 "Customizing: {template name}" 指示器及"另存为工作流"按钮

### Requirement: Edit agent roles in customized template — 编辑自定义模板中的 Agent 角色
用户应能够修改自定义模板中任何 agent 节点的 system prompt（角色描述）。

#### Scenario: Change agent role in template — 更改模板中的 Agent 角色
- **当** 用户在自定义模板中点击 agent 节点并更改角色描述
- **则** 节点的 `config.system_prompt` 应在画布上立即更新

### Requirement: Add and remove agent nodes in customized template — 在自定义模板中添加和删除 Agent 节点
用户应能够向自定义模板中添加新的 agent 节点，以及删除现有的 agent 节点。

#### Scenario: Add agent node to customized template — 向自定义模板添加 Agent 节点
- **当** 用户在自定义模式下从面板拖拽 agent 到画布上
- **则** 创建新的 agent 节点并连接到图谱

#### Scenario: Remove agent node from customized template — 从自定义模板删除 Agent 节点
- **当** 用户在自定义模式中选择一个 agent 节点并按 Delete
- **则** agent 节点及其连接的边应从画布上移除

### Requirement: Save customized template as new workflow — 将自定义模板保存为新工作流
用户应能够通过"另存为工作流"操作将自定义模板保存为新工作流。

#### Scenario: Save customized template — 保存自定义模板
- **当** 用户在自定义模式下点击"另存为工作流"
- **则** 系统应通过 `reactFlowToDsl()` 将当前画布状态转换为 Graph DSL JSON 并保存为新工作流

#### Scenario: Save requires workflow name — 保存需要工作流名称
- **当** 用户点击"另存为工作流"
- **则** 对话框应提示输入工作流名称后再保存

### Requirement: Original template remains unmodified — 原始模板保持不变
自定义模板不应修改原始模板。

#### Scenario: Template picker shows original template — 模板选择器显示原始模板
- **当** 用户将自定义模板保存为新工作流并重新打开模板选择器
- **则** 原始模板应仍出现在模板列表中，且未修改
