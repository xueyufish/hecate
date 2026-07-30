## MODIFIED Requirements — 修改后的需求

### Requirement: Form submission — 表单提交
The form SHALL submit all configured fields to the API. In create mode, it SHALL POST to `/api/agents`. In edit mode, it SHALL PUT to `/api/agents/{id}`. On success, it SHALL navigate to the agent detail page. If the API returns a 400 error for invalid `knowledge_base_ids`, the form SHALL display the validation error near the Knowledge Bases selector.

表单应将所有配置字段提交到 API。创建模式下应 POST 到 `/api/agents`，编辑模式下应 PUT 到 `/api/agents/{id}`。成功时应导航到 agent 详情页面。如果 API 因无效的 `knowledge_base_ids` 返回 400 错误，表单应在知识库选择器附近显示验证错误。

#### Scenario: Successful creation — 成功创建
- **WHEN** the user fills in required fields and clicks "Create"
- **THEN** the system SHALL POST to `/api/agents` and navigate to `/agents/{new_id}`
- **当**用户填写必填字段并点击"创建"
- **则**系统应 POST 到 `/api/agents` 并导航到 `/agents/{new_id}`

#### Scenario: Successful update — 成功更新
- **WHEN** the user modifies fields and clicks "Save"
- **THEN** the system SHALL PUT to `/api/agents/{id}` and show a success message
- **当**用户修改字段并点击"保存"
- **则**系统应 PUT 到 `/api/agents/{id}` 并显示成功消息

#### Scenario: Submission error — 提交错误
- **WHEN** the API returns an error
- **THEN** the system SHALL display the error message and keep the form data intact
- **当**API 返回错误
- **则**系统应显示错误消息并保持表单数据不变

#### Scenario: Invalid KB IDs error — 无效知识库 ID 错误
- **WHEN** the API returns 400 with invalid `knowledge_base_ids`
- **THEN** the system SHALL display the error message near the Knowledge Bases selector and keep the form data intact
- **当**API 返回 400 并包含无效的 `knowledge_base_ids`
- **则**系统应在知识库选择器附近显示错误消息并保持表单数据不变

### Requirement: Knowledge tab fields — 知识标签页字段
The Knowledge tab SHALL contain: Knowledge Bases (multi-select from available KBs) and Skills (multi-select from available skills). Deleted KBs SHALL NOT appear in the selector options.

知识标签页应包含：知识库（从可用知识库中多选）和技能（从可用技能中多选）。已删除的知识库不应出现在选择器选项中。

#### Scenario: Knowledge base selection — 知识库选择
- **WHEN** the user opens the Knowledge Bases selector
- **THEN** the system SHALL display all available (non-deleted) knowledge bases and allow multi-select
- **当**用户打开知识库选择器
- **则**系统应显示所有可用（未删除）知识库并允许多选

#### Scenario: Skill selection — 技能选择
- **WHEN** the user opens the Skills selector
- **THEN** the system SHALL display all available skills and allow multi-select
- **当**用户打开技能选择器
- **则**系统应显示所有可用技能并允许多选

#### Scenario: Empty state when no KBs/skills exist — 无知识库/技能时的空状态
- **WHEN** there are no knowledge bases or skills configured
- **THEN** the system SHALL display an empty state message with a link to create one
- **当**没有配置任何知识库或技能
- **则**系统应显示空状态消息并提供创建链接
