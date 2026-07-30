## MODIFIED Requirements — 修改的需求

### Requirement: Agent Configurator form layout — 需求：Agent 配置器表单布局
系统应提供 `AgentConfigurator` 组件，以选项卡形式展示 Agent 配置。表单应有 5 个选项卡：Basic、Knowledge、Tools、Memory 和 Advanced。该组件应支持创建模式（空表单）和编辑模式（预填充表单）。

#### Scenario: Create mode displays empty form — 场景：创建模式显示空表单
- **WHEN** 用户导航到 `/agents/new`
- **THEN** 系统应显示 AgentConfigurator，所有字段为空并带默认值

#### Scenario: Edit mode displays populated form — 场景：编辑模式显示已填充表单
- **WHEN** 用户导航到 `/agents/[id]`
- **THEN** 系统应获取 Agent 数据并显示 AgentConfigurator，字段预填充

#### Scenario: Tab navigation — 场景：选项卡导航
- **WHEN** 用户点击选项卡
- **THEN** 系统应显示对应部分，不丢失其他选项卡中的数据
