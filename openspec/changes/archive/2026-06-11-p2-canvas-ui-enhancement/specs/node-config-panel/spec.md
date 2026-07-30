## MODIFIED Requirements — 修改的需求

### Requirement: Node selection activates ConfigPanel in right-side panel — 节点选择激活右侧面板中的 ConfigPanel
当用户点击工作流画布上的节点时，系统应在右侧面板（300px 宽度）中显示 ConfigPanel 组件，并填充该节点的当前配置。对于 agent 节点，面板应显示增强的结构化表单（agent 选择器、角色描述、调用模式、通道选择器、模型覆盖），取代之前单一的 `agent_ref` 文本输入。

#### Scenario: Click a node to open configuration — 点击节点打开配置
- **当** 用户点击画布上的 conversation 节点
- **则** 右侧面板显示 ConfigPanel，节点的 model 和 system_prompt 字段已预填充

#### Scenario: Click an agent node to open enhanced configuration — 点击 Agent 节点打开增强配置
- **当** 用户点击画布上的 agent 节点
- **则** 右侧面板显示 ConfigPanel，包含 agent 结构化表单：agent 选择器下拉列表、角色描述文本域、调用模式单选、通道选择器和模型覆盖输入，所有字段均预填充节点的当前配置值

#### Scenario: Click canvas background to deselect — 点击画布背景取消选择
- **当** 用户点击空白画布区域（无节点）
- **则** 右侧面板显示占位文本"选择一个节点进行配置"，并清除选中的节点状态
