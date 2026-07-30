## MODIFIED Requirements — 修改后的需求

### Requirement: Orchestration template picker — 需求：编排模板选择器
The system SHALL provide a template picker accessible from the workflow canvas toolbar. Users SHALL be able to select a pre-built orchestration template which populates the canvas with the template's graph. Additionally, a pattern selector SHALL be provided as a separate toolbar action offering 6 collaboration patterns that auto-generate graph structures.

系统应提供一个可从工作流画布工具栏访问的模板选择器。用户应能选择预构建的编排模板，该模板将模板的图形填充到画布上。此外，还应提供一个模式选择器作为独立的工具栏操作，提供6种协作模式，可自动生成图形结构。

#### Scenario: User loads triage template — 场景：用户加载分类模板
- **WHEN** the user opens the template picker and selects "Customer Service Triage"
- **THEN** the canvas is populated with a router agent connected to 3 specialist agents via handoff edges

- **当**用户打开模板选择器并选择"客户服务分类"
- **则**画布会被填充为一个路由代理通过交接边连接到3个专业代理

#### Scenario: Template replaces current canvas — 场景：模板替换当前画布
- **WHEN** the user loads a template and the canvas already has nodes
- **THEN** the system prompts for confirmation before replacing the current canvas content

- **当**用户加载模板且画布已有节点时
- **则**系统在替换当前画布内容前提示确认

#### Scenario: Pattern selector accessible from toolbar — 场景：从工具栏访问模式选择器
- **WHEN** the user clicks the "Patterns" button in the canvas toolbar
- **THEN** a pattern selector dialog opens showing 6 collaboration patterns as selectable cards

- **当**用户点击画布工具栏中的"模式"按钮
- **则**打开一个模式选择器对话框，显示6种协作模式作为可选卡片
