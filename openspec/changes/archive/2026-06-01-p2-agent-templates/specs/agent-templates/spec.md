## ADDED Requirements — 新增需求

### Requirement: Agent Template Schema — 需求：Agent 模板 Schema
系统应定义 Agent 模板 schema，包含字段：name、description、category、preview（icon、tags）和 agent configuration（persona、model_config、tools、skills、knowledge_base_ids、risk_level、opening_remarks、enable_suggestions、memory_blocks）。

#### Scenario: Template structure — 场景：模板结构
- **WHEN** 模板 JSON 文件被加载
- **THEN** 它应包含元数据（name、description、category、preview）和与 AgentCreateSchema 匹配的 Agent 配置

### Requirement: Template List API — 需求：模板列表 API
系统应提供 `GET /api/agent-templates`，返回可用模板的列表，包含元数据（id、name、description、category、preview）。

#### Scenario: List templates — 场景：列出模板
- **WHEN** 用户请求 `GET /api/agent-templates`
- **THEN** 系统应返回所有内置模板及其元数据

### Requirement: Template Detail API — 需求：模板详情 API
系统应提供 `GET /api/agent-templates/{id}`，返回完整的模板配置。

#### Scenario: Get template details — 场景：获取模板详情
- **WHEN** 用户请求 `GET /api/agent-templates/{id}`
- **THEN** 系统应返回完整模板，包括 Agent 配置

#### Scenario: Template not found — 场景：模板未找到
- **WHEN** 用户请求不存在的模板
- **THEN** 系统应返回 HTTP 404

### Requirement: Template Instantiation — 需求：模板实例化
系统应提供 `POST /api/agent-templates/{id}/instantiate`，验证模板配置并返回用于 Agent 创建的配置。

#### Scenario: Instantiate template — 场景：实例化模板
- **WHEN** 用户请求 `POST /api/agent-templates/{id}/instantiate`
- **THEN** 系统应验证 KB ID 存在，并返回可用于 `POST /api/agents` 的模板配置

#### Scenario: Invalid KB IDs in template — 场景：模板中的无效 KB ID
- **WHEN** 模板引用不存在的 KB ID
- **THEN** 系统应返回 HTTP 422，列出无效的 KB ID

### Requirement: Built-in Templates — 需求：内置模板
系统应包含 5 个内置模板：
1. **Customer Service** — 客服角色设定，工单查询工具
2. **Code Review** — 代码分析角色设定，文件读取工具
3. **Research Assistant** — 研究角色设定，网页搜索工具
4. **Content Writer** — 写作角色设定，内容生成工具
5. **Data Analyst** — 数据分析角色设定，数据处理工具

#### Scenario: Built-in templates available — 场景：内置模板可用
- **WHEN** 系统启动
- **THEN** 所有 5 个内置模板应通过 API 可用

### Requirement: Frontend Template Picker — 需求：前端模板选择器
Agent 创建页面应显示"From Template"按钮，打开模板选择器对话框。用户应能按类别浏览模板，选择后预填充 Agent 创建表单。

#### Scenario: Open template picker — 场景：打开模板选择器
- **WHEN** 用户在 Agent 创建页面点击"From Template"
- **THEN** 系统应显示包含模板类别和预览的对话框

#### Scenario: Select template — 场景：选择模板
- **WHEN** 用户选择模板
- **THEN** 系统应关闭对话框，并用模板的配置预填充 Agent 创建表单

#### Scenario: Cancel template selection — 场景：取消模板选择
- **WHEN** 用户关闭模板选择器而不选择
- **THEN** 表单应保持不变
