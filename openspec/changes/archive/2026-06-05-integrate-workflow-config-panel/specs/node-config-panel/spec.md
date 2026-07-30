## ADDED Requirements — 新增需求

### Requirement: 节点选择在右侧面板激活 ConfigPanel — 节点选择在右侧面板激活 ConfigPanel
当用户点击工作流画布上的节点时，系统 SHALL 在右侧面板（300px 宽度）中显示 ConfigPanel 组件，并填充该节点的当前配置。

#### Scenario: 点击节点打开配置
- **WHEN** 用户点击画布上的对话节点
- **THEN** 右侧面板显示 ConfigPanel，节点的 model 和 system_prompt 字段预先填充

#### Scenario: 点击画布背景取消选择
- **WHEN** 用户点击空白画布区域（无节点）
- **THEN** 右侧面板显示占位文本"Select a node to configure"，选中节点状态被清除

### Requirement: ConfigPanel 编辑传播到画布并自动保存 — ConfigPanel 编辑传播到画布并自动保存
当用户在 ConfigPanel 中编辑节点属性时，变更 SHALL 更新画布上的节点数据并触发现有的自动保存机制（2 秒去抖到 API）。

#### Scenario: 编辑对话节点中的模型名称
- **WHEN** 用户在 ConfigPanel 中将模型字段从 "gpt-4o" 改为 "gpt-4o-mini"
- **THEN** 画布上的 node.data.config.model 更新，2 秒后变更通过 PUT /api/workflows/{id} 保存到后端

### Requirement: 右侧面板宽度为 300px — 右侧面板宽度为 300px
右侧面板 SHALL 具有固定的 300px 宽度，与 ConfigPanel 组件内置宽度匹配。

#### Scenario: 面板宽度一致性
- **WHEN** 工作流编辑器页面加载
- **THEN** 右侧面板渲染为正好 300px 宽

### Requirement: 测试运行结果保持在底部面板 — 测试运行结果保持在底部面板
右侧面板 SHALL 专用于节点配置编辑。测试运行输入表单和执行结果 SHALL 保持在现有的底部面板和右侧结果显示区域中。

#### Scenario: 点击节点后测试运行结果显示
- **WHEN** 用户有测试运行结果并点击节点进行配置
- **THEN** 右侧面板显示 ConfigPanel，测试运行结果在底部面板中仍然可访问