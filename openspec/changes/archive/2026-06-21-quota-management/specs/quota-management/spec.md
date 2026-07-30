## ADDED Requirements — 新增需求

### Requirement: QuotaModel ORM model — 需求：QuotaModel ORM 模型
The system SHALL define `QuotaModel(BaseModel)` in `models/quota.py` with fields: `name` (String 255), `resource_type` (String 32, one of QuotaResourceType: requests, tokens, cost), `scope` (String 16, one of QuotaScope: workspace, api_key), `scope_id` (UUID, the workspace or API key ID), `limit_value` (Float, hard cap), `soft_limit` (Float, nullable, warning threshold), `window_type` (String 16, one of QuotaWindowType: rolling_minute, daily, monthly), `enforcement` (String 16, one of EnforcementMode: hard_reject, soft_allow, default hard_reject), `enabled` (Boolean, default True), `workspace_id` (UUID, default zero UUID).

系统应在 `models/quota.py` 中定义 `QuotaModel(BaseModel)`，包含字段：`name`（String 255）、`resource_type`（String 32，QuotaResourceType 之一：requests、tokens、cost）、`scope`（String 16，QuotaScope 之一：workspace、api_key）、`scope_id`（UUID，工作空间或 API 密钥 ID）、`limit_value`（Float，硬上限）、`soft_limit`（Float，可空，警告阈值）、`window_type`（String 16，QuotaWindowType 之一：rolling_minute、daily、monthly）、`enforcement`（String 16，EnforcementMode 之一：hard_reject、soft_allow，默认 hard_reject）、`enabled`（Boolean，默认 True）、`workspace_id`（UUID，默认零 UUID）。

#### Scenario: Create a workspace monthly cost quota — 场景：创建工作空间月度成本配额
- **WHEN** a `QuotaModel` is created with `resource_type="cost"`, `scope="workspace"`, `scope_id=<workspace_uuid>`, `limit_value=1000.0`, `soft_limit=800.0`, `window_type="monthly"`
- **THEN** the record is persisted with `enforcement="hard_reject"` and `enabled=True`

- **当**创建 `QuotaModel`，设置 `resource_type="cost"`、`scope="workspace"`、`scope_id=<workspace_uuid>`、`limit_value=1000.0`、`soft_limit=800.0`、`window_type="monthly"`
- **则**记录以 `enforcement="hard_reject"` 和 `enabled=True` 持久化

#### Scenario: Create an API key RPM quota — 场景：创建 API 密钥 RPM 配额
- **WHEN** a `QuotaModel` is created with `resource_type="requests"`, `scope="api_key"`, `scope_id=<key_uuid>`, `limit_value=100`, `window_type="rolling_minute"`
- **THEN** the record is persisted and the rate limiter SHALL use this limit for that API key

- **当**创建 `QuotaModel`，设置 `resource_type="requests"`、`scope="api_key"`、`scope_id=<key_uuid>`、`limit_value=100`、`window_type="rolling_minute"`
- **则**记录被持久化，速率限制器应使用此限制处理该 API 密钥

#### Scenario: Disabled quota is not enforced — 场景：禁用的配额不强制执行
- **WHEN** a quota has `enabled=False`
- **THEN** the enforcement middleware and post-LLM recording SHALL skip it

- **当**配额设置 `enabled=False`
- **则**强制执行中间件和 LLM 后记录应跳过它

### Requirement: QuotaUsageModel ORM model — 需求：QuotaUsageModel ORM 模型
The system SHALL define `QuotaUsageModel(BaseModel)` in `models/quota.py` with fields: `quota_id` (UUID FK to QuotaModel), `period_start` (DateTime), `period_end` (DateTime), `used_value` (Float, default 0), `last_updated` (DateTime), `workspace_id` (UUID).

系统应在 `models/quota.py` 中定义 `QuotaUsageModel(BaseModel)`，包含字段：`quota_id`（UUID FK 到 QuotaModel）、`period_start`（DateTime）、`period_end`（DateTime）、`used_value`（Float，默认 0）、`last_updated`（DateTime）、`workspace_id`（UUID）。

#### Scenario: Usage record created on first consumption — 场景：首次消费时创建用量记录
- **WHEN** a request or LLM call consumes resources against a quota for the first time in a period
- **THEN** a `QuotaUsageModel` record is created with `period_start` at the window start, `period_end` at the window end, and `used_value` set to the consumed amount

- **当**请求或 LLM 调用在周期内首次消耗配额资源
- **则**创建 `QuotaUsageModel` 记录，`period_start` 为窗口开始，`period_end` 为窗口结束，`used_value` 设置为消耗量

#### Scenario: Usage record accumulates within a period — 场景：周期内用量记录累加
- **WHEN** subsequent consumption occurs within the same period
- **THEN** `used_value` is incremented atomically and `last_updated` is refreshed

- **当**同一周期内发生后续消费
- **则** `used_value` 原子递增，`last_updated` 刷新

#### Scenario: Usage record resets at period boundary — 场景：周期边界用量记录重置
- **WHEN** the current time exceeds `period_end` and a new consumption occurs
- **THEN** a new `QuotaUsageModel` record is created for the next period, and the old record remains for historical querying

- **当**当前时间超过 `period_end` 且发生新消费
- **则**为下一周期创建新的 `QuotaUsageModel` 记录，旧记录保留供历史查询

### Requirement: Quota definition CRUD API — 需求：配额定义 CRUD API
The system SHALL expose REST endpoints for quota management under `/api/quotas`.

系统应在 `/api/quotas` 下公开配额管理的 REST 端点。

#### Scenario: Create a quota — 场景：创建配额
- **WHEN** `POST /api/quotas` is called with a valid quota schema
- **THEN** a new quota definition is created and returned with status 201

- **当**使用有效配额模式调用 `POST /api/quotas`
- **则**创建新的配额定义并以状态 201 返回

#### Scenario: List quotas for current workspace — 场景：列出当前工作空间的配额
- **WHEN** `GET /api/quotas` is called
- **THEN** all quota definitions for the current workspace are returned

- **当**调用 `GET /api/quotas`
- **则**返回当前工作空间的所有配额定义

#### Scenario: List quotas filtered by resource type — 场景：按资源类型过滤列出配额
- **WHEN** `GET /api/quotas?resource_type=cost` is called
- **THEN** only cost-type quotas are returned

- **当**调用 `GET /api/quotas?resource_type=cost`
- **则**仅返回成本类型配额

#### Scenario: Update a quota — 场景：更新配额
- **WHEN** `PUT /api/quotas/{id}` is called with an updated `limit_value`
- **THEN** the quota is updated and returned

- **当**使用更新后的 `limit_value` 调用 `PUT /api/quotas/{id}`
- **则**配额被更新并返回

#### Scenario: Delete a quota — 场景：删除配额
- **WHEN** `DELETE /api/quotas/{id}` is called
- **THEN** the quota is soft-deleted and status 204 is returned

- **当**调用 `DELETE /api/quotas/{id}`
- **则**配额被软删除，返回状态 204

### Requirement: Quota usage query API — 需求：配额用量查询 API
The system SHALL expose `GET /api/quotas/usage` that returns current usage for all quotas in the current workspace.

系统应公开 `GET /api/quotas/usage`，返回当前工作空间中所有配额的当前用量。

#### Scenario: Query usage for all quotas — 场景：查询所有配额的用量
- **WHEN** `GET /api/quotas/usage` is called
- **THEN** a list of `{quota_id, name, resource_type, limit_value, used_value, remaining, period_start, period_end, utilization_pct}` entries is returned

- **当**调用 `GET /api/quotas/usage`
- **则**返回 `{quota_id, name, resource_type, limit_value, used_value, remaining, period_start, period_end, utilization_pct}` 条目列表

#### Scenario: Query usage filtered by resource type — 场景：按资源类型过滤查询用量
- **WHEN** `GET /api/quotas/usage?resource_type=tokens` is called
- **THEN** only token-type quota usage is returned

- **当**调用 `GET /api/quotas/usage?resource_type=tokens`
- **则**仅返回 Token 类型配额用量

### Requirement: Quota reset API — 需求：配额重置 API
The system SHALL expose `POST /api/quotas/{id}/reset` that manually resets the current period's usage to zero. This endpoint requires workspace admin role.

系统应公开 `POST /api/quotas/{id}/reset`，手动将当前周期的用量重置为零。此端点需要工作空间管理员角色。

#### Scenario: Admin resets a quota period — 场景：管理员重置配额周期
- **WHEN** `POST /api/quotas/{id}/reset` is called by a workspace admin
- **THEN** the current `QuotaUsageModel.used_value` is set to 0 and `last_updated` is refreshed

- **当**工作空间管理员调用 `POST /api/quotas/{id}/reset`
- **则**当前 `QuotaUsageModel.used_value` 设置为 0，`last_updated` 刷新

#### Scenario: Non-admin cannot reset — 场景：非管理员无法重置
- **WHEN** `POST /api/quotas/{id}/reset` is called by a non-admin user
- **THEN** a 403 Forbidden response is returned

- **当**非管理员用户调用 `POST /api/quotas/{id}/reset`
- **则**返回 403 Forbidden 响应

### Requirement: Quota enforcement middleware for request counts — 需求：请求计数的配额强制执行中间件
The system SHALL enforce request-count quotas (RPM, daily requests) via a FastAPI middleware that runs after auth context resolution. When a hard-reject quota is exceeded, the middleware SHALL return HTTP 429 with `Retry-After` and `X-Quota-*` response headers.

系统应通过 FastAPI 中间件强制执行请求计数配额（RPM、每日请求数），该中间件在认证上下文解析后运行。当超过硬拒绝配额时，中间件应返回 HTTP 429，并包含 `Retry-After` 和 `X-Quota-*` 响应头。

#### Scenario: RPM quota exceeded — 场景：RPM 配额超出
- **WHEN** a workspace has an API-key-scoped RPM quota of 100 and the 101st request arrives within the same minute
- **THEN** the middleware returns 429 with `Retry-After: 60` and `X-Quota-Limit-Requests: 100`

- **当**工作空间有 API 密钥范围的 100 RPM 配额，且同一分钟内第 101 个请求到达
- **则**中间件返回 429，包含 `Retry-After: 60` 和 `X-Quota-Limit-Requests: 100`

#### Scenario: Daily request quota exceeded — 场景：每日请求配额超出
- **WHEN** a workspace has a daily request quota of 5000 and the 5001st request arrives
- **THEN** the middleware returns 429 with `X-Quota-Limit-Requests: 5000` and `X-Quota-Reset-Requests: <seconds until UTC midnight>`

- **当**工作空间有 5000 的每日请求配额，且第 5001 个请求到达
- **则**中间件返回 429，包含 `X-Quota-Limit-Requests: 5000` 和 `X-Quota-Reset-Requests: <距离 UTC 午夜秒数>`

#### Scenario: Request allowed when under quota — 场景：配额内时允许请求
- **WHEN** a workspace's RPM quota is 100 and only 50 requests have been made this minute
- **THEN** the middleware passes the request through and adds `X-Quota-Remaining-Requests: 50` to the response

- **当**工作空间的 RPM 配额为 100，且该分钟内仅发出 50 个请求
- **则**中间件通过请求，并在响应中添加 `X-Quota-Remaining-Requests: 50`

#### Scenario: No quota configured — 场景：未配置配额
- **WHEN** no quota definitions exist for the workspace or API key
- **THEN** the middleware passes the request through without adding quota headers

- **当**工作空间或 API 密钥没有配额定义
- **则**中间件通过请求，不添加配额头

### Requirement: Post-LLM quota recording for tokens and cost — 需求：Token 和成本的 LLM 后配额记录
The system SHALL record actual token usage and cost against applicable quotas after each LLM call completes. When a soft_limit is crossed, the system SHALL trigger an alert via the Alerting system.

系统应在每次 LLM 调用完成后记录实际的 Token 使用量和成本到适用配额。当超过 soft_limit 时，系统应通过告警系统触发告警。

#### Scenario: Record token usage after LLM call — 场景：LLM 调用后记录 Token 用量
- **WHEN** an LLM call completes with `usage={prompt_tokens: 1000, completion_tokens: 500}` and the workspace has a daily token quota
- **THEN** the daily token quota's `used_value` is incremented by 1500

- **当** LLM 调用完成，`usage={prompt_tokens: 1000, completion_tokens: 500}`，且工作空间有每日 Token 配额
- **则**每日 Token 配额的 `used_value` 增加 1500

#### Scenario: Record cost after LLM call — 场景：LLM 调用后记录成本
- **WHEN** an LLM call completes with a computed cost of $0.0075 and the workspace has a monthly cost quota
- **THEN** the monthly cost quota's `used_value` is incremented by 0.0075

- **当** LLM 调用完成，计算成本为 0.0075 美元，且工作空间有月度成本配额
- **则**月度成本配额的 `used_value` 增加 0.0075

#### Scenario: Soft limit crossed triggers alert — 场景：超过软限制触发告警
- **WHEN** a post-LLM recording causes `used_value` to cross the quota's `soft_limit` threshold for the first time in the period
- **THEN** an alert event SHALL be created via the Alerting system with alert_type `quota_soft_limit_reached`

- **当** LLM 后记录导致 `used_value` 在周期内首次超过配额的 `soft_limit` 阈值
- **则**应通过告警系统创建 alert_type 为 `quota_soft_limit_reached` 的告警事件

#### Scenario: Hard limit exceeded on next request — 场景：下次请求超出硬限制
- **WHEN** `used_value` exceeds `limit_value` for a hard_reject token quota
- **THEN** the next LLM request from that workspace SHALL be rejected with 429 before processing

- **当** hard_reject Token 配额的 `used_value` 超过 `limit_value`
- **则**该工作空间的下一个 LLM 请求应在处理前以 429 拒绝

### Requirement: Quota period auto-reset — 需求：配额周期自动重置
The system SHALL automatically create new usage periods when the current period expires. Daily periods reset at UTC midnight, monthly periods reset on the 1st of each month at UTC 00:00.

系统应在当前周期到期时自动创建新的用量周期。每日周期在 UTC 午夜重置，月度周期在每月第 1 天 UTC 00:00 重置。

#### Scenario: Daily period auto-reset — 场景：每日周期自动重置
- **WHEN** the current time is past a daily quota usage record's `period_end` and a new request arrives
- **THEN** a new `QuotaUsageModel` record is created with `period_start` at today's UTC midnight, `period_end` at tomorrow's UTC midnight, and `used_value` initialized to the current request's consumption

- **当**当前时间超过每日配额用量记录的 `period_end` 且新请求到达
- **则**创建新的 `QuotaUsageModel` 记录，`period_start` 为今日 UTC 午夜，`period_end` 为明日 UTC 午夜，`used_value` 初始化为当前请求的消耗量

#### Scenario: Monthly period auto-reset — 场景：月度周期自动重置
- **WHEN** the current time is past a monthly quota usage record's `period_end` and a new request arrives
- **THEN** a new `QuotaUsageModel` record is created with `period_start` at the 1st of the current month UTC, `period_end` at the 1st of the next month UTC

- **当**当前时间超过月度配额用量记录的 `period_end` 且新请求到达
- **则**创建新的 `QuotaUsageModel` 记录，`period_start` 为当月第 1 天 UTC，`period_end` 为下月第 1 天 UTC

### Requirement: Standard quota response headers — 需求：标准配额响应头
The system SHALL include quota-related response headers on API responses when quota enforcement is active.

当配额强制执行激活时，系统应在 API 响应中包含配额相关的响应头。

#### Scenario: Headers on successful response — 场景：成功响应的头
- **WHEN** a request succeeds and the workspace has quota definitions
- **THEN** the response includes `X-Quota-Limit-Requests`, `X-Quota-Remaining-Requests`, `X-Quota-Reset-Requests` headers

- **当**请求成功且工作空间有配额定义
- **则**响应包含 `X-Quota-Limit-Requests`、`X-Quota-Remaining-Requests`、`X-Quota-Reset-Requests` 头

#### Scenario: Headers on 429 response — 场景：429 响应的头
- **WHEN** a request is rejected due to quota exceeded
- **THEN** the 429 response includes `Retry-After`, `X-Quota-Limit-*`, `X-Quota-Remaining-*` (zero), and `X-Quota-Reset-*` headers

- **当**请求因超出配额被拒绝
- **则**429 响应包含 `Retry-After`、`X-Quota-Limit-*`、`X-Quota-Remaining-*`（零）和 `X-Quota-Reset-*` 头

### Requirement: Quota definition caching — 需求：配额定义缓存
The system SHALL cache quota definitions in memory per-process with a TTL of 60 seconds to avoid querying the database on every request.

系统应在每个进程的内存中缓存配额定义，TTL 为 60 秒，以避免每次请求都查询数据库。

#### Scenario: Cache hit avoids database query — 场景：缓存命中避免数据库查询
- **WHEN** a quota check is performed and the definition is in cache
- **THEN** no database query for quota definitions is issued

- **当**执行配额检查且定义在缓存中
- **则**不发出配额定义的数据库查询

#### Scenario: Cache refresh after TTL — 场景：TTL 后缓存刷新
- **WHEN** 60 seconds have passed since the last cache load
- **THEN** the next quota check SHALL reload definitions from the database

- **当**自上次缓存加载后已过去 60 秒
- **则**下一次配额检查应从数据库重新加载定义
