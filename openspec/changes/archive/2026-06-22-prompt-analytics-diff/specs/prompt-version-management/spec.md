## ADDED Requirements — 新增需求

### Requirement: Commit message on prompt version — 需求：提示版本的提交消息
The system SHALL support a `commit_message: str | None` field on `PromptVersionModel`. When provided during prompt update, the commit message SHALL be persisted on the newly created version record.

系统应在 `PromptVersionModel` 上支持 `commit_message: str | None` 字段。在提示更新期间提供时，提交消息应持久化在新创建的版本记录上。

#### Scenario: Update prompt with commit message — 场景：带提交消息更新提示
- **WHEN** `PUT /api/prompts/{id}` is called with `{"template": "...", "commit_message": "Add citation instructions"}`
- **THEN** the new version SHALL be created with `commit_message` persisted and returned in the version response

- **当**使用 `{"template": "...", "commit_message": "Add citation instructions"}` 调用 `PUT /api/prompts/{id}`
- **则**新版本应创建，`commit_message` 被持久化并在版本响应中返回

#### Scenario: Update prompt without commit message — 场景：不带提交消息更新提示
- **WHEN** `PUT /api/prompts/{id}` is called without a `commit_message` field
- **THEN** the new version SHALL be created with `commit_message=None`

- **当**调用 `PUT /api/prompts/{id}` 时未提供 `commit_message` 字段
- **则**新版本应创建，`commit_message=None`

#### Scenario: Version listing includes commit messages — 场景：版本列表包含提交消息
- **WHEN** `GET /api/prompts/{id}/versions` is called
- **THEN** each version in the response SHALL include its `commit_message` field (may be null)

- **当**调用 `GET /api/prompts/{id}/versions`
- **则**响应中的每个版本应包含其 `commit_message` 字段（可能为 null）

### Requirement: Protected label enforcement — 需求：受保护标签的强制执行
The system SHALL enforce role-based access control on protected prompt labels. Labels listed in `PROTECTED_PROMPT_LABELS` config (default: `["production"]`) SHALL require `admin` role to add or remove. Non-admin users attempting to modify protected labels SHALL receive 403 Forbidden.

系统应在受保护的提示标签上强制执行基于角色的访问控制。`PROTECTED_PROMPT_LABELS` 配置（默认：`["production"]`）中列出的标签需要 `admin` 角色才能添加或移除。尝试修改受保护标签的非管理员用户应收到 403 Forbidden。

#### Scenario: Admin adds protected label — 场景：管理员添加受保护标签
- **WHEN** a user with `admin` role updates a prompt adding the "production" label
- **THEN** the label SHALL be persisted on the new version

- **当**具有 `admin` 角色的用户更新提示并添加 "production" 标签
- **则**标签应在新版本上持久化

#### Scenario: Non-admin blocked from protected label — 场景：非管理员被阻止使用受保护标签
- **WHEN** a user with `editor` role attempts to add the "production" label
- **THEN** the API SHALL return 403 Forbidden with an error message indicating the label is protected

- **当**具有 `editor` 角色的用户尝试添加 "production" 标签
- **则** API 应返回 403 Forbidden，并带有一条指示该标签受保护的错误消息

#### Scenario: Non-admin can modify non-protected labels — 场景：非管理员可以修改非受保护标签
- **WHEN** a user with `editor` role updates a prompt adding the "development" label
- **THEN** the update SHALL succeed since "development" is not in PROTECTED_PROMPT_LABELS

- **当**具有 `editor` 角色的用户更新提示并添加 "development" 标签
- **则**更新应成功，因为 "development" 不在 PROTECTED_PROMPT_LABELS 中

### Requirement: Prompt version trace linkage — 需求：提示版本追踪关联
The system SHALL write prompt identification into TraceModel metadata when LLMWorker executes using a configured prompt. The metadata SHALL include `prompt_id` (UUID string) and `prompt_version` (integer) when the agent configuration references a prompt.

当 LLMWorker 使用配置的提示执行时，系统应将提示标识写入 TraceModel 元数据。当代理配置引用提示时，元数据应包含 `prompt_id`（UUID 字符串）和 `prompt_version`（整数）。

#### Scenario: LLM call with prompt writes trace metadata — 场景：带提示的 LLM 调用写入追踪元数据
- **WHEN** LLMWorker executes an LLM call for an agent with `prompt_id` configured
- **THEN** the resulting TraceModel record SHALL have `metadata_.prompt_id` and `metadata_.prompt_version` populated

- **当** LLMWorker 为配置了 `prompt_id` 的代理执行 LLM 调用
- **则**生成的 TraceModel 记录应填充 `metadata_.prompt_id` 和 `metadata_.prompt_version`

#### Scenario: LLM call without prompt skips metadata — 场景：无提示的 LLM 调用跳过元数据
- **WHEN** LLMWorker executes an LLM call for an agent without `prompt_id` configured
- **THEN** no prompt metadata SHALL be written to the trace record

- **当** LLMWorker 为未配置 `prompt_id` 的代理执行 LLM 调用
- **则**不应将提示元数据写入追踪记录
