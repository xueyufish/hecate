## ADDED Requirements — 新增需求

### Requirement: Agent Export Format — 需求：代理导出格式
系统应定义一种导出 JSON 格式，包含以下字段：`version`（字符串）、`exported_at`（ISO 时间戳）、`agent`（配置对象）、`workflow`（可选的 Graph DSL）、`memory_blocks`（块配置列表）。

#### Scenario: Export structure — 场景：导出结构
- **当** 代理被导出时
- **那么** JSON 应包含 `version`、`exported_at`、带有所有配置字段（name、persona、model_config、mode、tools、skills、knowledge_base_ids、risk_level、opening_remarks、enable_suggestions）的 `agent`，以及可选的 `workflow` 和 `memory_blocks`

### Requirement: Agent Export Endpoint — 需求：代理导出端点
系统应提供 `GET /api/agents/{id}/export`，返回代理配置作为可下载的 JSON 文件。

#### Scenario: Export agent — 场景：导出代理
- **当** 用户请求 `GET /api/agents/{id}/export`
- **那么** 系统应返回带有 `Content-Disposition: attachment` 头的 JSON 文件

#### Scenario: Export agent with workflow — 场景：导出包含工作流的代理
- **当** 代理具有 `mode=workflow` 且关联了工作流
- **那么** 导出应在 `workflow` 字段中包含工作流的 Graph DSL

#### Scenario: Export agent with memory blocks — 场景：导出包含内存块的代理
- **当** 代理具有内存块
- **那么** 导出应在 `memory_blocks` 字段中包含内存块

#### Scenario: Export non-existent agent — 场景：导出不存在的代理
- **当** 用户请求导出不存在的代理
- **那么** 系统应返回 HTTP 404

### Requirement: Agent Import Endpoint — 需求：代理导入端点
系统应提供 `POST /api/agents/import`，接受 JSON 文件并从导出的配置创建新代理。

#### Scenario: Import agent — 场景：导入代理
- **当** 用户提交带有有效导出 JSON 的 `POST /api/agents/import`
- **那么** 系统应使用导出的配置创建新代理并返回代理数据

#### Scenario: Import with workflow — 场景：导入包含工作流
- **当** 导出包含 `workflow` 字段
- **那么** 系统应创建新的工作流并链接到导入的代理

#### Scenario: Import with memory blocks — 场景：导入包含内存块
- **当** 导出包含 `memory_blocks`
- **那么** 系统应为新代理创建内存块

#### Scenario: Import with missing KBs — 场景：导入时 KB 缺失
- **当** 导出引用的 KB ID 在目标环境中不存在
- **那么** 系统应记录警告并在不关联这些 KB 的情况下导入代理

#### Scenario: Import invalid JSON — 场景：导入无效 JSON
- **当** 提交的 JSON 无效或缺少必填字段
- **那么** 系统应返回包含验证错误的 HTTP 422

### Requirement: Frontend Export Button — 需求：前端导出按钮
代理详情页面应显示一个"导出"按钮，用于下载 JSON 格式的代理配置。

#### Scenario: Export from detail page — 场景：从详情页面导出
- **当** 用户在代理详情页面上点击"导出"
- **那么** 浏览器应下载名为 `{agent-name}.json` 的 JSON 文件

### Requirement: Frontend Import Button — 需求：前端导入按钮
代理列表页面应显示一个"导入代理"按钮，打开文件上传对话框。用户应能够选择 JSON 文件进行导入。

#### Scenario: Import from list page — 场景：从列表页面导入
- **当** 用户点击"导入代理"并选择一个 JSON 文件
- **那么** 系统应上传文件、创建代理，并导航到新代理的详情页面

#### Scenario: Import with errors — 场景：导入出错
- **当** 导入因无效 JSON 或缺少必填字段而失败
- **那么** 系统应显示错误消息
