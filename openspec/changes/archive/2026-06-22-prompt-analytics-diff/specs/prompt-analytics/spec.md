## ADDED Requirements — 新增需求

### Requirement: Prompt version diff API — 需求：提示版本差异 API
The system SHALL expose `GET /api/prompts/{id}/diff?from_version=X&to_version=Y` that computes a line-level diff between two prompt versions using `difflib`, returning structured diff entries with added/removed/context lines, line numbers, commit messages, and token count delta.

系统应公开 `GET /api/prompts/{id}/diff?from_version=X&to_version=Y`，使用 `difflib` 计算两个提示版本之间的行级差异，返回包含添加/删除/上下文行、行号、提交消息和 Token 数量差值的结构化差异条目。

#### Scenario: Diff between two versions — 场景：两个版本之间的差异
- **WHEN** `GET /api/prompts/{id}/diff?from_version=2&to_version=3` is called
- **THEN** the response SHALL include `diff_entries` array with `{type, from_line, to_line, content}`, `added_lines` count, `removed_lines` count, `token_delta`, and both versions' commit messages

- **当**调用 `GET /api/prompts/{id}/diff?from_version=2&to_version=3`
- **则**响应应包含 `diff_entries` 数组（包含 `{type, from_line, to_line, content}`）、`added_lines` 计数、`removed_lines` 计数、`token_delta` 以及两个版本的提交消息

#### Scenario: Diff with identical versions — 场景：相同版本之间的差异
- **WHEN** a diff is requested between two versions with identical templates
- **THEN** the response SHALL have `added_lines=0`, `removed_lines=0`, and all entries as `type="context"`

- **当**请求两个具有相同模板的版本之间的差异
- **则**响应应具有 `added_lines=0`、`removed_lines=0`，且所有条目的 `type="context"`

#### Scenario: Diff with non-existent version — 场景：不存在的版本的差异
- **WHEN** a diff is requested with a version number that doesn't exist
- **THEN** the API SHALL return 404 Not Found

- **当**使用不存在的版本号请求差异
- **则** API 应返回 404 Not Found

### Requirement: Per-version analytics API — 需求：每个版本的分析 API
The system SHALL expose `GET /api/prompts/{id}/analytics?version=X&days=7` that aggregates trace-derived metrics for a specific prompt version by querying TraceModel records where `metadata_->>'prompt_id'` and `metadata_->>'prompt_version'` match.

系统应公开 `GET /api/prompts/{id}/analytics?version=X&days=7`，通过查询 `metadata_->>'prompt_id'` 和 `metadata_->>'prompt_version'` 匹配的 TraceModel 记录，聚合特定提示版本的追踪衍生指标。

#### Scenario: Analytics for active version — 场景：活跃版本的分析
- **WHEN** `GET /api/prompts/{id}/analytics?version=3&days=7` is called and traces exist with matching metadata
- **THEN** the response SHALL include `total_calls`, `avg_latency_ms`, `total_tokens`, `error_rate`, `total_cost`, and `daily_breakdown` array

- **当**调用 `GET /api/prompts/{id}/analytics?version=3&days=7` 且存在匹配元数据的追踪
- **则**响应应包含 `total_calls`、`avg_latency_ms`、`total_tokens`、`error_rate`、`total_cost` 和 `daily_breakdown` 数组

#### Scenario: Analytics for version with no traces — 场景：无追踪的版本的分析
- **WHEN** analytics is requested for a version that has no trace data
- **THEN** the response SHALL return zero values for all metrics (`total_calls=0`, `avg_latency_ms=0`, etc.)

- **当**为没有追踪数据的版本请求分析
- **则**响应应对所有指标返回零值（`total_calls=0`、`avg_latency_ms=0` 等）

### Requirement: Version comparison API — 需求：版本比较 API
The system SHALL expose `GET /api/prompts/{id}/compare?from_version=X&to_version=Y` that returns side-by-side metrics for two prompt versions, enabling data-driven deployment decisions.

系统应公开 `GET /api/prompts/{id}/compare?from_version=X&to_version=Y`，返回两个提示版本的并排指标，支持数据驱动的部署决策。

#### Scenario: Compare two versions — 场景：比较两个版本
- **WHEN** `GET /api/prompts/{id}/compare?from_version=2&to_version=3` is called
- **THEN** the response SHALL include per-version metrics (calls, avg latency, tokens, error rate, cost) and delta values showing the difference

- **当**调用 `GET /api/prompts/{id}/compare?from_version=2&to_version=3`
- **则**响应应包含每个版本的指标（调用次数、平均延迟、Token 数、错误率、成本）和显示差异的差值

### Requirement: AI-assisted change summary API — 需求：AI 辅助变更摘要 API
The system SHALL expose `POST /api/prompts/{id}/versions/{version}/summary` that generates a human-readable change description by sending the version diff to LLMService for summarization.

系统应公开 `POST /api/prompts/{id}/versions/{version}/summary`，通过将版本差异发送到 LLMService 进行摘要，生成人类可读的变更描述。

#### Scenario: Generate summary for version with changes — 场景：为有变更的版本生成摘要
- **WHEN** the summary endpoint is called for a version that differs from its predecessor
- **THEN** the response SHALL include a natural language summary describing what changed (e.g., "Added instructions about citing sources and changed tone to be more formal")

- **当**为与其前身不同的版本调用摘要端点
- **则**响应应包含描述变更的自然语言摘要（例如"添加了关于引用来源的说明并将语气改为更正式"）

#### Scenario: Generate summary for first version — 场景：为第一个版本生成摘要
- **WHEN** the summary endpoint is called for version 1 (no predecessor)
- **THEN** the response SHALL indicate this is the initial version with no changes to summarize

- **当**为版本 1（无前身）调用摘要端点
- **则**响应应指明这是初始版本，没有需要摘要的变更

### Requirement: Prompt analytics service — 需求：提示分析服务
The system SHALL provide a `PromptAnalyticsService` in `services/prompt_analytics_service.py` that computes version diffs, aggregates trace metrics per prompt version, compares two versions' metrics, and generates AI change summaries via LLMService.

系统应在 `services/prompt_analytics_service.py` 中提供 `PromptAnalyticsService`，计算版本差异、按提示版本聚合追踪指标、比较两个版本的指标，并通过 LLMService 生成 AI 变更摘要。

#### Scenario: Compute diff between versions — 场景：计算版本之间的差异
- **WHEN** `compute_diff(prompt_id, from_version, to_version)` is called
- **THEN** the service SHALL fetch both PromptVersionModel records, compute difflib diff, count additions/removals, estimate token delta, and return a structured diff result

- **当**调用 `compute_diff(prompt_id, from_version, to_version)`
- **则**服务应获取两个 PromptVersionModel 记录，计算 difflib 差异，统计添加/删除，估计 Token 差值，并返回结构化的差异结果

#### Scenario: Aggregate metrics for a version — 场景：聚合版本的指标
- **WHEN** `get_version_analytics(prompt_id, version, days)` is called
- **THEN** the service SHALL query TraceModel filtering by metadata prompt_id and prompt_version, aggregate call count, latency, tokens, error rate, and cost via CostService

- **当**调用 `get_version_analytics(prompt_id, version, days)`
- **则**服务应通过 metadata 的 prompt_id 和 prompt_version 过滤查询 TraceModel，通过 CostService 聚合调用次数、延迟、Token 数、错误率和成本
