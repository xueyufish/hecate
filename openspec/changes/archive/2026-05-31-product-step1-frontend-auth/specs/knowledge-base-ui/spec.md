## ADDED Requirements — 新增需求

### Requirement: Knowledge base list — 需求：知识库列表
系统应显示所有知识库，包含名称、文档数和创建日期。

#### Scenario: View knowledge base list — 场景：查看知识库列表
- **WHEN** 用户导航到 Knowledge Bases 页面
- **THEN** 系统显示知识库列表，包含名称、文档数和"Create"按钮

### Requirement: Create knowledge base — 需求：创建知识库
系统应允许用户使用名称和描述创建知识库。

#### Scenario: Successful creation — 场景：创建成功
- **WHEN** 用户填写名称和可选描述，点击 Create
- **THEN** 系统通过 `POST /api/knowledge-bases` 创建知识库，并重定向到详情页面

### Requirement: Upload documents — 需求：上传文档
系统应允许用户向知识库上传文档。

#### Scenario: Upload single document — 场景：上传单个文档
- **WHEN** 用户选择文件（PDF、DOCX、TXT、MD）并点击 Upload
- **THEN** 系统通过 `POST /api/knowledge-bases/{id}/upload` 上传并显示上传进度

#### Scenario: Supported formats — 场景：支持的格式
- **WHEN** 用户选择不支持的文- 件类型
- **THEN** 系统显示验证错误，指示支持的格式

### Requirement: Document status display — 需求：文档状态展示
系统应显示每个上传文档的解析状态。

#### Scenario: View document list — 场景：查看文档列表
- **WHEN** 用户打开知识库详情页面
- **THEN** 系统显示所有文档，包含文件名、文件大小、解析状态（pending/parsing/completed/failed）和分块数

#### Scenario: Failed document — 场景：解析失败的文档
- **WHEN** 文档的 parsing_status 为"failed"
- **THEN** 系统显示 parsing_error 字段中的错误消息

### Requirement: Delete knowledge base — 需求：删除知识库
系统应允许用户通过确认后删除知识库。

#### Scenario: Delete with confirmation — 场景：确认后删除
- **WHEN** 用户点击删除并确认
- **THEN** 系统删除知识库并重定向到列表页面
