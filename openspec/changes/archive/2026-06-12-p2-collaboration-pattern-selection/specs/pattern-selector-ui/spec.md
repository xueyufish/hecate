## ADDED Requirements — 新增需求

### Requirement: Pattern selector card grid — 需求：模式选择器卡片网格
The system SHALL display a pattern selector as a card grid dialog accessible from the workflow canvas toolbar. Each of the 6 patterns (Sequential, Parallel, Handoff, Broadcast, Negotiation, Debate) SHALL be shown as a card with an icon, name, short description, and a mini topological preview.

系统应显示一个模式选择器，作为可从工作流画布工具栏访问的卡片网格对话框。6种模式（顺序、并行、交接、广播、协商、辩论）中的每一种都应显示为带有图标、名称、简短描述和微型拓扑预览的卡片。

#### Scenario: Pattern selector opened from toolbar — 场景：从工具栏打开模式选择器
- **WHEN** the user clicks the "Patterns" button in the canvas toolbar
- **THEN** a dialog SHALL open showing 6 pattern cards arranged in a 3×2 grid layout

- **当**用户点击画布工具栏中的"模式"按钮
- **则**应打开一个对话框，显示以3×2网格布局排列的6张模式卡片

#### Scenario: Each pattern card shows metadata — 场景：每张模式卡片显示元数据
- **WHEN** the pattern selector dialog is displayed
- **THEN** each card SHALL show a pattern icon, pattern name, one-line description, and a mini graph preview (simplified node-edge diagram)

- **当**模式选择器对话框显示时
- **则**每张卡片应显示模式图标、模式名称、一行描述和一个微型图形预览（简化的节点-边图）

#### Scenario: Pattern card indicates node count — 场景：模式卡片指示节点数量
- **WHEN** the user views the "Parallel" pattern card
- **THEN** the card SHALL display the estimated minimum node count (e.g., "5+ nodes")

- **当**用户查看"并行"模式卡片
- **则**卡片应显示估计的最小节点数量（例如"5个以上节点"）

### Requirement: Pattern configuration dialog — 需求：模式配置对话框
After selecting a pattern, the system SHALL show a configuration dialog with pattern-specific parameter fields. The dialog SHALL validate inputs before enabling the "Generate" button.

选择模式后，系统应显示一个包含模式特定参数字段的配置对话框。对话框应在启用"生成"按钮前验证输入。

#### Scenario: Sequential pattern configuration — 场景：顺序模式配置
- **WHEN** the user selects the "Sequential" pattern
- **THEN** a configuration dialog SHALL appear with fields: workflow name (text), stages (dynamic list where each stage has: name, model dropdown, system prompt textarea), and an "Add Stage" button

- **当**用户选择"顺序"模式
- **则**应出现一个配置对话框，包含字段：工作流名称（文本）、阶段（动态列表，每个阶段包含：名称、模型下拉框、系统提示文本区域）和"添加阶段"按钮

#### Scenario: Parallel pattern configuration — 场景：并行模式配置
- **WHEN** the user selects the "Parallel" pattern
- **THEN** the configuration dialog SHALL show fields: coordinator (name, model, prompt), workers (dynamic list with add/remove), aggregator (name, model, prompt)

- **当**用户选择"并行"模式
- **则**配置对话框应显示字段：协调器（名称、模型、提示）、工作者（带添加/移除的动态列表）、聚合器（名称、模型、提示）

#### Scenario: Handoff pattern configuration — 场景：交接模式配置
- **WHEN** the user selects the "Handoff" pattern
- **THEN** the configuration dialog SHALL show fields: router (name, model, prompt), specialists (dynamic list with add/remove)

- **当**用户选择"交接"模式
- **则**配置对话框应显示字段：路由器（名称、模型、提示）、专家（带添加/移除的动态列表）

#### Scenario: Minimum validation before generate — 场景：生成前的最小验证
- **WHEN** the user has not filled in all required fields for a pattern
- **THEN** the "Generate" button SHALL be disabled and missing fields SHALL show validation errors

- **当**用户未填完模式的所有必填字段
- **则**"生成"按钮应被禁用，缺失字段应显示验证错误

### Requirement: Pattern selection generates canvas graph — 需求：模式选择生成画布图形
When the user completes pattern configuration and clicks "Generate", the system SHALL call the pattern generation API, convert the resulting Graph DSL JSON to React Flow elements via `dslToReactFlow()`, and populate the canvas.

当用户完成模式配置并点击"生成"时，系统应调用模式生成 API，通过 `dslToReactFlow()` 将生成的 Graph DSL JSON 转换为 React Flow 元素，并填充画布。

#### Scenario: Generate and populate canvas — 场景：生成并填充画布
- **WHEN** the user fills in a 3-stage sequential pattern and clicks "Generate"
- **THEN** the system SHALL call `POST /api/collaboration-patterns/sequential/generate`, receive the Graph DSL, convert it via `dslToReactFlow()`, and replace the canvas with the generated nodes and edges

- **当**用户填写3阶段顺序模式并点击"生成"
- **则**系统应调用 `POST /api/collaboration-patterns/sequential/generate`，接收 Graph DSL，通过 `dslToReactFlow()` 转换，并用生成的节点和边替换画布

#### Scenario: Canvas replaces existing content with confirmation — 场景：画布替换现有内容需确认
- **WHEN** the user generates a pattern and the canvas already has nodes
- **THEN** the system SHALL show a confirmation dialog before replacing existing canvas content

- **当**用户生成模式且画布已有节点时
- **则**系统应在替换现有画布内容前显示确认对话框

#### Scenario: Enter customization mode after generation — 场景：生成后进入自定义模式
- **WHEN** the pattern graph is loaded onto the canvas
- **THEN** the canvas SHALL enter template customization mode (same as template loading), enabling full editing and "Save as Workflow"

- **当**模式图加载到画布上时
- **则**画布应进入模板自定义模式（与模板加载相同），启用完整编辑和"保存为工作流"

### Requirement: Pattern selector alongside template picker — 需求：模式选择器与模板选择器并存
The pattern selector SHALL be accessible as a separate toolbar action alongside the existing template picker, clearly differentiated as "Start from Pattern" vs "Load Template".

模式选择器应作为独立的工具栏操作与现有模板选择器并存，明确区分为"从模式开始"和"加载模板"。

#### Scenario: Both options visible in toolbar — 场景：两个选项在工具栏中均可见
- **WHEN** the user views the canvas toolbar
- **THEN** both "Patterns" and "Templates" buttons SHALL be visible with distinct icons and labels

- **当**用户查看画布工具栏
- **则**"模式"和"模板"两个按钮均应可见，并带有不同的图标和标签
