## ADDED Requirements — 新增需求

### Requirement: Agent list page — 需求：Agent 列表页面
系统应以列表形式展示当前用户拥有的所有 Agent，包含名称、模型和状态。

#### Scenario: View agent list — 场景：查看 Agent 列表
- **WHEN** 用户导航到 Agents 页面
- **THEN** 系统显示 Agent 列表，包含名称、模型名称、创建日期和"Create Agent"按钮

#### Scenario: Empty state — 场景：空状态
- **WHEN** 用户没有 Agent
- **THEN** 系统显示空状态，引导创建第一个 Agent

### Requirement: Create agent — 需求：创建 Agent
系统应允许用户使用名称、描述、模型选择和系统提示词创建新 Agent。

#### Scenario: Successful creation — 场景：创建成功
- **WHEN** 用户填写 Agent 名称，选择模型，可选设置系统提示词，点击 Create
- **THEN** 系统通过 API 创建 Agent 并重定向到 Agent 详情页面

#### Scenario: Model selection — 场景：模型选择
- **WHEN** 用户打开模型选择器
- **THEN** 系统显示从 `GET /v1/models` 获取的可用模型

### Requirement: Agent detail and configuration — 需求：Agent 详情和配置
系统应允许用户查看和编辑 Agent 配置，包括工具和知识库。

#### Scenario: View agent config — 场景：查看 Agent 配置
- **WHEN** 用户打开 Agent 的详情页面
- **THEN** 系统显示当前配置：名称、描述、模型、系统提示词、绑定的工具、绑定的知识库

#### Scenario: Bind tools — 场景：绑定工具
- **WHEN** 用户打开工具绑定部分
- **THEN** 系统显示可用工具（来自 `GET /api/tools`），带切换开关绑定/解绑每个工具

#### Scenario: Bind knowledge bases — 场景：绑定知识库
- **WHEN** 用户打开知识库绑定部分
- **THEN** 系统显示可用知识库（来自 `GET /api/knowledge-bases`），带切换开关绑定/解绑

### Requirement: Delete agent — 需求：删除 Agent
系统应允许用户通过确认后删除 Agent。

#### Scenario: Delete with confirmation — 场景：确认后删除
- **WHEN** 用户点击删除并确认
- **THEN** 系统删除 Agent 并重定向到 Agent 列表
