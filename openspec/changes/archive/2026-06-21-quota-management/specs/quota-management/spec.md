## ADDED Requirements

### Requirement: QuotaModel ORM model
The system SHALL define `QuotaModel(BaseModel)` in `models/quota.py` with fields: `name` (String 255), `resource_type` (String 32, one of QuotaResourceType: requests, tokens, cost), `scope` (String 16, one of QuotaScope: workspace, api_key), `scope_id` (UUID, the workspace or API key ID), `limit_value` (Float, hard cap), `soft_limit` (Float, nullable, warning threshold), `window_type` (String 16, one of QuotaWindowType: rolling_minute, daily, monthly), `enforcement` (String 16, one of EnforcementMode: hard_reject, soft_allow, default hard_reject), `enabled` (Boolean, default True), `workspace_id` (UUID, default zero UUID).

#### Scenario: Create a workspace monthly cost quota
- **WHEN** a `QuotaModel` is created with `resource_type="cost"`, `scope="workspace"`, `scope_id=<workspace_uuid>`, `limit_value=1000.0`, `soft_limit=800.0`, `window_type="monthly"`
- **THEN** the record is persisted with `enforcement="hard_reject"` and `enabled=True`

#### Scenario: Create an API key RPM quota
- **WHEN** a `QuotaModel` is created with `resource_type="requests"`, `scope="api_key"`, `scope_id=<key_uuid>`, `limit_value=100`, `window_type="rolling_minute"`
- **THEN** the record is persisted and the rate limiter SHALL use this limit for that API key

#### Scenario: Disabled quota is not enforced
- **WHEN** a quota has `enabled=False`
- **THEN** the enforcement middleware and post-LLM recording SHALL skip it

### Requirement: QuotaUsageModel ORM model
The system SHALL define `QuotaUsageModel(BaseModel)` in `models/quota.py` with fields: `quota_id` (UUID FK to QuotaModel), `period_start` (DateTime), `period_end` (DateTime), `used_value` (Float, default 0), `last_updated` (DateTime), `workspace_id` (UUID).

#### Scenario: Usage record created on first consumption
- **WHEN** a request or LLM call consumes resources against a quota for the first time in a period
- **THEN** a `QuotaUsageModel` record is created with `period_start` at the window start, `period_end` at the window end, and `used_value` set to the consumed amount

#### Scenario: Usage record accumulates within a period
- **WHEN** subsequent consumption occurs within the same period
- **THEN** `used_value` is incremented atomically and `last_updated` is refreshed

#### Scenario: Usage record resets at period boundary
- **WHEN** the current time exceeds `period_end` and a new consumption occurs
- **THEN** a new `QuotaUsageModel` record is created for the next period, and the old record remains for historical querying

### Requirement: Quota definition CRUD API
The system SHALL expose REST endpoints for quota management under `/api/quotas`.

#### Scenario: Create a quota
- **WHEN** `POST /api/quotas` is called with a valid quota schema
- **THEN** a new quota definition is created and returned with status 201

#### Scenario: List quotas for current workspace
- **WHEN** `GET /api/quotas` is called
- **THEN** all quota definitions for the current workspace are returned

#### Scenario: List quotas filtered by resource type
- **WHEN** `GET /api/quotas?resource_type=cost` is called
- **THEN** only cost-type quotas are returned

#### Scenario: Update a quota
- **WHEN** `PUT /api/quotas/{id}` is called with an updated `limit_value`
- **THEN** the quota is updated and returned

#### Scenario: Delete a quota
- **WHEN** `DELETE /api/quotas/{id}` is called
- **THEN** the quota is soft-deleted and status 204 is returned

### Requirement: Quota usage query API
The system SHALL expose `GET /api/quotas/usage` that returns current usage for all quotas in the current workspace.

#### Scenario: Query usage for all quotas
- **WHEN** `GET /api/quotas/usage` is called
- **THEN** a list of `{quota_id, name, resource_type, limit_value, used_value, remaining, period_start, period_end, utilization_pct}` entries is returned

#### Scenario: Query usage filtered by resource type
- **WHEN** `GET /api/quotas/usage?resource_type=tokens` is called
- **THEN** only token-type quota usage is returned

### Requirement: Quota reset API
The system SHALL expose `POST /api/quotas/{id}/reset` that manually resets the current period's usage to zero. This endpoint requires workspace admin role.

#### Scenario: Admin resets a quota period
- **WHEN** `POST /api/quotas/{id}/reset` is called by a workspace admin
- **THEN** the current `QuotaUsageModel.used_value` is set to 0 and `last_updated` is refreshed

#### Scenario: Non-admin cannot reset
- **WHEN** `POST /api/quotas/{id}/reset` is called by a non-admin user
- **THEN** a 403 Forbidden response is returned

### Requirement: Quota enforcement middleware for request counts
The system SHALL enforce request-count quotas (RPM, daily requests) via a FastAPI middleware that runs after auth context resolution. When a hard-reject quota is exceeded, the middleware SHALL return HTTP 429 with `Retry-After` and `X-Quota-*` response headers.

#### Scenario: RPM quota exceeded
- **WHEN** a workspace has an API-key-scoped RPM quota of 100 and the 101st request arrives within the same minute
- **THEN** the middleware returns 429 with `Retry-After: 60` and `X-Quota-Limit-Requests: 100`

#### Scenario: Daily request quota exceeded
- **WHEN** a workspace has a daily request quota of 5000 and the 5001st request arrives
- **THEN** the middleware returns 429 with `X-Quota-Limit-Requests: 5000` and `X-Quota-Reset-Requests: <seconds until UTC midnight>`

#### Scenario: Request allowed when under quota
- **WHEN** a workspace's RPM quota is 100 and only 50 requests have been made this minute
- **THEN** the middleware passes the request through and adds `X-Quota-Remaining-Requests: 50` to the response

#### Scenario: No quota configured
- **WHEN** no quota definitions exist for the workspace or API key
- **THEN** the middleware passes the request through without adding quota headers

### Requirement: Post-LLM quota recording for tokens and cost
The system SHALL record actual token usage and cost against applicable quotas after each LLM call completes. When a soft_limit is crossed, the system SHALL trigger an alert via the Alerting system (8.6).

#### Scenario: Record token usage after LLM call
- **WHEN** an LLM call completes with `usage={prompt_tokens: 1000, completion_tokens: 500}` and the workspace has a daily token quota
- **THEN** the daily token quota's `used_value` is incremented by 1500

#### Scenario: Record cost after LLM call
- **WHEN** an LLM call completes with a computed cost of $0.0075 and the workspace has a monthly cost quota
- **THEN** the monthly cost quota's `used_value` is incremented by 0.0075

#### Scenario: Soft limit crossed triggers alert
- **WHEN** a post-LLM recording causes `used_value` to cross the quota's `soft_limit` threshold for the first time in the period
- **THEN** an alert event SHALL be created via the Alerting system (8.6) with alert_type `quota_soft_limit_reached`

#### Scenario: Hard limit exceeded on next request
- **WHEN** `used_value` exceeds `limit_value` for a hard_reject token quota
- **THEN** the next LLM request from that workspace SHALL be rejected with 429 before processing

### Requirement: Quota period auto-reset
The system SHALL automatically create new usage periods when the current period expires. Daily periods reset at UTC midnight, monthly periods reset on the 1st of each month at UTC 00:00.

#### Scenario: Daily period auto-reset
- **WHEN** the current time is past a daily quota usage record's `period_end` and a new request arrives
- **THEN** a new `QuotaUsageModel` record is created with `period_start` at today's UTC midnight, `period_end` at tomorrow's UTC midnight, and `used_value` initialized to the current request's consumption

#### Scenario: Monthly period auto-reset
- **WHEN** the current time is past a monthly quota usage record's `period_end` and a new request arrives
- **THEN** a new `QuotaUsageModel` record is created with `period_start` at the 1st of the current month UTC, `period_end` at the 1st of the next month UTC

### Requirement: Standard quota response headers
The system SHALL include quota-related response headers on API responses when quota enforcement is active.

#### Scenario: Headers on successful response
- **WHEN** a request succeeds and the workspace has quota definitions
- **THEN** the response includes `X-Quota-Limit-Requests`, `X-Quota-Remaining-Requests`, `X-Quota-Reset-Requests` headers

#### Scenario: Headers on 429 response
- **WHEN** a request is rejected due to quota exceeded
- **THEN** the 429 response includes `Retry-After`, `X-Quota-Limit-*`, `X-Quota-Remaining-*` (zero), and `X-Quota-Reset-*` headers

### Requirement: Quota definition caching
The system SHALL cache quota definitions in memory per-process with a TTL of 60 seconds to avoid querying the database on every request.

#### Scenario: Cache hit avoids database query
- **WHEN** a quota check is performed and the definition is in cache
- **THEN** no database query for quota definitions is issued

#### Scenario: Cache refresh after TTL
- **WHEN** 60 seconds have passed since the last cache load
- **THEN** the next quota check SHALL reload definitions from the database
