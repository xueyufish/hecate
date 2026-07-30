## ADDED Requirements — 新增需求

### Requirement: Memory Block Editor Component — 需求：内存块编辑器组件
系统应提供 `MemoryBlockEditor` 组件，展示 Agent 的内存块列表，支持内联编辑功能。每个块应显示其标签、内容预览、位置和 token 限制。

#### Scenario: Display memory blocks — 场景：显示内存块
- **WHEN** 在 Agent 配置器中打开 Memory 选项卡
- **THEN** 系统应显示该 Agent 的所有内存块，按 position 排序，显示标签、内容预览（前 100 字符）和 limit

#### Scenario: Edit block content inline — 场景：内联编辑块内容
- **WHEN** 用户点击块的内容区域
- **THEN** 系统应进入编辑模式，显示 textarea 和 Save/Cancel 按钮

#### Scenario: Save edited block — 场景：保存编辑的块
- **WHEN** 用户编辑内容并点击 Save
- **THEN** 系统应调用 `PUT /api/agents/{id}/memory-blocks/{block_id}` 并更新显示的内容

#### Scenario: Cancel editing — 场景：取消编辑
- **WHEN** 用户在编辑中点击 Cancel
- **THEN** 系统应恢复原始内容，不调用 API

#### Scenario: Delete a block — 场景：删除块
- **WHEN** 用户点击块的删除按钮
- **THEN** 系统应显示确认对话框，然后调用 `DELETE /api/agents/{id}/memory-blocks/{block_id}`

### Requirement: Memory Block Templates — 需求：内存块模板
系统应为常见内存块类型提供预定义模板。用户应能一键添加模板块，将使用建议内容和设置创建块。

#### Scenario: Add template block — 场景：添加模板块
- **WHEN** 用户从模板下拉列表中选择模板（如"Persona"）
- **THEN** 系统应使用模板的标签、内容提示、位置和限制调用 `POST /api/agents/{id}/memory-blocks`

#### Scenario: Template already exists — 场景：模板已存在
- **WHEN** 用户选择的模板标签在 Agent 中已存在
- **THEN** 系统应显示错误消息，指示该块已存在

#### Scenario: Available templates — 场景：可用模板
- **WHEN** 模板下拉列表被打开
- **THEN** 系统应显示模板：persona、user_profile、domain_context、task_tracker

### Requirement: Create Custom Memory Block — 需求：创建自定义内存块
系统应提供用于创建自定义内存块的表单，支持用户定义的标签、内容、位置和限制。

#### Scenario: Open create form — 场景：打开创建表单
- **WHEN** 用户点击"Add Block"按钮
- **THEN** 系统应显示包含字段的表单：label（必填）、content、position（默认 0）、limit（默认 2000）

#### Scenario: Submit custom block — 场景：提交自定义块
- **WHEN** 用户填写表单并点击 Create
- **THEN** 系统应调用 `POST /api/agents/{id}/memory-blocks` 并将新块添加到列表

#### Scenario: Duplicate label error — 场景：重复标签错误
- **WHEN** 用户提交的块标签已存在
- **THEN** 系统应显示 409 冲突错误消息

### Requirement: Memory Block Indicators in Chat — 需求：聊天中的内存块指示器
聊天页面应显示徽章，展示当前 Agent 对话中哪些内存块处于活跃状态。每个徽章应显示块标签。

#### Scenario: Agent with memory blocks — 场景：有内存块的 Agent
- **WHEN** 用户与有内存块的 Agent 聊天
- **THEN** 聊天 UI 应在聊天头部附近为每个活跃块标签显示徽章

#### Scenario: Agent with no memory blocks — 场景：没有内存块的 Agent
- **WHEN** 用户与没有内存块的 Agent 聊天
- **THEN** 聊天 UI 不应显示任何内存块指示器

### Requirement: Memory Blocks on Agent Detail Page — 需求：Agent 详情页面上的内存块
Agent 详情页面应显示一个部分，展示 Agent 的内存块及其标签和内容预览。用户应能从此部分导航到 Agent 配置器的 Memory 选项卡。

#### Scenario: Agent detail with blocks — 场景：有块的 Agent 详情
- **WHEN** 用户查看有内存块的 Agent 详情页面
- **THEN** 系统应显示"Memory Blocks"部分，包含块标签、内容预览和指向配置器的"Edit"链接

#### Scenario: Agent detail with no blocks — 场景：没有块的 Agent 详情
- **WHEN** 用户查看没有内存块的 Agent 详情页面
- **THEN** 系统应显示"No memory blocks configured"及添加链接
