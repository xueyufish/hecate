## ADDED Requirements

### Requirement: AlertRuleModel ORM model
The system SHALL define `AlertRuleModel(BaseModel)` in `models/alert.py` with fields: `name` (String 255), `description` (String, nullable), `alert_type` (String, one of AlertType enum), `threshold` (Float), `window_minutes` (Integer, evaluation window), `for_minutes` (Integer, sustained duration before firing), `severity` (String, one of AlertSeverity enum), `filters` (JSON, optional scope filters: agent_id, model), `enabled` (Boolean, default True), `escalation_policy_id` (UUID FK, nullable), `channel_ids` (JSON, list of NotificationChannel UUIDs), `workspace_id` (UUID, default zero UUID).

#### Scenario: Create a rule with all fields
- **WHEN** an `AlertRuleModel` is created with `name="High Error Rate"`, `alert_type="error_rate"`, `threshold=0.05`, `window_minutes=5`, `for_minutes=2`, `severity="critical"`
- **THEN** the record is persisted with `enabled=True` and the specified values

#### Scenario: Create a rule with scope filter
- **WHEN** an `AlertRuleModel` is created with `filters={"agent_id": "<uuid>", "model": "gpt-4o"}`
- **THEN** the evaluator SHALL only evaluate traces matching those filters when assessing this rule

#### Scenario: Disabled rule is not evaluated
- **WHEN** a rule has `enabled=False`
- **THEN** the evaluator SHALL skip it during evaluation cycles

### Requirement: AlertEventModel ORM model
The system SHALL define `AlertEventModel(BaseModel)` in `models/alert.py` with fields: `rule_id` (UUID FK to AlertRuleModel), `state` (String, one of AlertState enum: pending, firing, resolved, acked), `current_value` (Float, the metric value that triggered the alert), `fired_at` (DateTime, nullable), `resolved_at` (DateTime, nullable), `acked_at` (DateTime, nullable), `acked_by` (UUID, nullable), `escalation_step` (Integer, default 0), `workspace_id` (UUID).

#### Scenario: Event created in pending state
- **WHEN** a rule's threshold is first breached
- **THEN** an `AlertEventModel` is created with `state="pending"`, `current_value` set to the triggering value, `escalation_step=0`

#### Scenario: Event transitions to firing
- **WHEN** a pending event's `for_minutes` duration has elapsed and the condition still holds
- **THEN** the event's `state` SHALL be updated to `"firing"` and `fired_at` SHALL be set to the current timestamp

#### Scenario: Event transitions to resolved
- **WHEN** a firing or pending event's condition no longer holds
- **THEN** the event's `state` SHALL be updated to `"resolved"` and `resolved_at` SHALL be set to the current timestamp

#### Scenario: Event acknowledged manually
- **WHEN** a user calls the ACK endpoint on a firing event
- **THEN** the event's `state` SHALL be updated to `"acked"`, `acked_at` set to current timestamp, `acked_by` set to the user's ID, and escalation SHALL stop

### Requirement: AlertSilenceModel ORM model
The system SHALL define `AlertSilenceModel(BaseModel)` in `models/alert.py` with fields: `start_at` (DateTime), `end_at` (DateTime), `matchers` (JSON: optional keys `rule_ids` list, `severity` list), `created_by` (UUID, nullable), `reason` (String, nullable), `workspace_id` (UUID).

#### Scenario: Silence suppresses matching notifications
- **WHEN** a firing event matches a silence's matchers and the current time is within `[start_at, end_at]`
- **THEN** no notification SHALL be dispatched for that event

#### Scenario: Silence auto-expires
- **WHEN** the current time exceeds `end_at`
- **THEN** the silence SHALL no longer suppress notifications, even if matchers would match

#### Scenario: Silence scoped to specific rules
- **WHEN** a silence has `matchers={"rule_ids": ["<uuid1>", "<uuid2>"]}`
- **THEN** only events from those rules SHALL be silenced; events from other rules SHALL still notify

### Requirement: EscalationPolicyModel ORM model
The system SHALL define `EscalationPolicyModel(BaseModel)` in `models/alert.py` with fields: `name` (String 255), `steps` (JSON array of `{delay_min: int, channel_types: [str]}`), `repeat_interval_min` (Integer, nullable, repeat cadence for re-sending step 0), `workspace_id` (UUID).

#### Scenario: Default escalation policy seeded by migration
- **WHEN** the migration is applied
- **THEN** a default policy named `"Standard Escalation"` SHALL exist with steps `[{delay_min: 0, channel_types: ["webhook_feishu", "websocket"]}, {delay_min: 15, channel_types: ["email"]}]` and `repeat_interval_min=60`

#### Scenario: Escalation progresses through steps
- **WHEN** a firing event has `fired_at` 20 minutes ago and the policy has steps at delay 0 and 15
- **THEN** the evaluator SHALL have dispatched step 0 (at fire time) and step 1 (at +15 min), and `escalation_step` SHALL be 1

#### Scenario: Escalation stops on ACK
- **WHEN** an event is ACKED
- **THEN** no further escalation steps SHALL be dispatched, even if `repeat_interval_min` has elapsed

#### Scenario: Repeat interval re-sends step 0
- **WHEN** a firing event is not ACKED and `repeat_interval_min` has elapsed since the last dispatch
- **THEN** step 0 channels SHALL be re-dispatched

### Requirement: NotificationChannelModel ORM model
The system SHALL define `NotificationChannelModel(BaseModel)` in `models/alert.py` with fields: `name` (String 255), `channel_type` (String, one of ChannelType enum), `config` (JSON: type-specific config, e.g., `url` for webhooks, `recipients` for email), `enabled` (Boolean, default True), `workspace_id` (UUID).

#### Scenario: Create a Feishu webhook channel
- **WHEN** a `NotificationChannelModel` is created with `channel_type="webhook_feishu"`, `config={"url": "https://open.feishu.cn/open-apis/bot/v2/hook/xxx"}`
- **THEN** the channel is available for assignment to alert rules

#### Scenario: Create an email channel
- **WHEN** a `NotificationChannelModel` is created with `channel_type="email"`, `config={"recipients": ["admin@company.com"]}`
- **THEN** the channel SHALL dispatch emails to the specified recipients when notified

#### Scenario: Disabled channel is skipped
- **WHEN** a channel has `enabled=False`
- **THEN** the dispatcher SHALL skip it during notification dispatch

### Requirement: Alert rule CRUD API
The system SHALL expose REST endpoints for alert rule management under `/api/alerts/rules`.

#### Scenario: Create a rule
- **WHEN** `POST /api/alerts/rules` is called with a valid rule schema
- **THEN** a new rule is created and returned with status 201

#### Scenario: List rules
- **WHEN** `GET /api/alerts/rules` is called
- **THEN** a paginated list of rules for the current workspace is returned, ordered by `created_at` descending

#### Scenario: Update a rule
- **WHEN** `PUT /api/alerts/rules/{id}` is called with updated `threshold`
- **THEN** the rule is updated and returned

#### Scenario: Delete a rule
- **WHEN** `DELETE /api/alerts/rules/{id}` is called
- **THEN** the rule is soft-deleted and status 204 is returned

### Requirement: Alert event query and ACK API
The system SHALL expose REST endpoints for alert event management under `/api/alerts/events`.

#### Scenario: List events filtered by state
- **WHEN** `GET /api/alerts/events?state=firing` is called
- **THEN** only events with `state="firing"` are returned, ordered by `fired_at` descending

#### Scenario: List events filtered by rule
- **WHEN** `GET /api/alerts/events?rule_id=<uuid>` is called
- **THEN** only events for the specified rule are returned

#### Scenario: Acknowledge an event
- **WHEN** `POST /api/alerts/events/{id}/ack` is called
- **THEN** the event's `state` is updated to `"acked"`, `acked_at` and `acked_by` are set, and status 200 is returned

### Requirement: Silence window CRUD API
The system SHALL expose REST endpoints for silence management under `/api/alerts/silences`.

#### Scenario: Create a silence window
- **WHEN** `POST /api/alerts/silences` is called with `start_at`, `end_at`, `matchers`, and `reason`
- **THEN** a new silence record is created and returned with status 201

#### Scenario: List active silences
- **WHEN** `GET /api/alerts/silences?active=true` is called
- **THEN** only silences where `start_at <= now() <= end_at` are returned

#### Scenario: Delete a silence
- **WHEN** `DELETE /api/alerts/silences/{id}` is called
- **THEN** the silence is deleted and status 204 is returned

### Requirement: Notification channel CRUD API
The system SHALL expose REST endpoints for notification channel management under `/api/alerts/channels`.

#### Scenario: Create a channel
- **WHEN** `POST /api/alerts/channels` is called with a valid channel type and config
- **THEN** a new channel is created and returned with status 201

#### Scenario: List channels
- **WHEN** `GET /api/alerts/channels` is called
- **THEN** all channels for the current workspace are returned

#### Scenario: Test a channel
- **WHEN** `POST /api/alerts/channels/{id}/test` is called
- **THEN** a test notification SHALL be dispatched to the channel and the result (success/failure) SHALL be returned

### Requirement: Escalation policy CRUD API
The system SHALL expose REST endpoints for escalation policy management under `/api/alerts/escalation-policies`.

#### Scenario: Create a custom policy
- **WHEN** `POST /api/alerts/escalation-policies` is called with steps and repeat interval
- **THEN** a new policy is created and returned with status 201

#### Scenario: List policies
- **WHEN** `GET /api/alerts/escalation-policies` is called
- **THEN** all policies for the current workspace are returned, including the default seeded policy

### Requirement: Alert evaluator with advisory lock
The system SHALL run an `AlertEvaluator` asyncio background task that acquires a PostgreSQL session-level advisory lock, loads enabled rules and active silences, evaluates each rule's signal against its threshold, manages event state transitions, and dispatches notifications. The evaluator SHALL run at a configurable interval (default 60 seconds).

#### Scenario: Evaluator acquires advisory lock
- **WHEN** the evaluator starts an evaluation cycle
- **THEN** it SHALL call `pg_try_advisory_lock(<constant_id>)` and proceed only if the lock is acquired

#### Scenario: Evaluator skips when lock unavailable
- **WHEN** another node holds the advisory lock
- **THEN** the evaluator SHALL skip the cycle and wait for the next interval

#### Scenario: Evaluator evaluates error_rate rule
- **WHEN** a rule with `alert_type="error_rate"`, `threshold=0.05`, `window_minutes=5` is evaluated
- **THEN** the evaluator SHALL query `COUNT(status='error') / COUNT(*)` from TraceModel in the last 5 minutes (with rule filters applied) and compare against 0.05

#### Scenario: Evaluator evaluates cost_monthly_forecast rule
- **WHEN** a rule with `alert_type="cost_monthly_forecast"`, `threshold=1000.0` is evaluated
- **THEN** the evaluator SHALL compute the projected monthly cost using weighted moving average of recent daily costs and compare against the threshold

### Requirement: SignalProvider registry for 8 alert types
The system SHALL implement a `SignalProvider` registry with one provider per `AlertType`. Each provider queries `TraceModel` or `CostService` for the relevant metric within the specified window and returns the current value.

#### Scenario: Error rate signal provider
- **WHEN** the `error_rate` provider is queried with `window_minutes=5` and filters `{"agent_id": "<uuid>"}`
- **THEN** it SHALL return `COUNT(status='error') / COUNT(*)` from TraceModel where `start_time >= now() - 5 minutes` and `agent_id` matches

#### Scenario: Latency P95 signal provider
- **WHEN** the `latency_p95` provider is queried with `window_minutes=10`
- **THEN** it SHALL return the 95th percentile of `(end_time - start_time)` in milliseconds for completed traces in the window

#### Scenario: TTFT signal provider
- **WHEN** the `latency_ttft` provider is queried with `window_minutes=10`
- **THEN** it SHALL return the average `metadata->ttft_ms` from GENERATION-type traces in the window

#### Scenario: Token usage signal provider
- **WHEN** the `token_usage` provider is queried with `window_minutes=60`
- **THEN** it SHALL return `SUM(usage->total_tokens)` from GENERATION traces in the window

#### Scenario: Cost daily signal provider
- **WHEN** the `cost_daily` provider is queried
- **THEN** it SHALL delegate to `CostService.get_cost_summary()` for the current day and return `total_cost`

#### Scenario: Cost monthly forecast signal provider
- **WHEN** the `cost_monthly_forecast` provider is queried
- **THEN** it SHALL compute `recent_avg_daily_cost * days_in_month` using exponentially-weighted moving average of the last 7 days and return the projected value

#### Scenario: Tool failure rate signal provider
- **WHEN** the `tool_failure_rate` provider is queried with `window_minutes=10`
- **THEN** it SHALL return `COUNT(status='error' AND type='TOOL') / COUNT(type='TOOL')` from TraceModel in the window

#### Scenario: Success rate signal provider
- **WHEN** the `success_rate` provider is queried with `window_minutes=5`
- **THEN** it SHALL return `COUNT(status='completed') / COUNT(*)` from TraceModel in the window

### Requirement: NotificationDispatcher with built-in templates
The system SHALL implement a `NotificationDispatcher` that routes firing alert events through the appropriate escalation policy steps to the configured notification channels. Each channel type SHALL use a built-in message template that formats the alert into the platform's native format.

#### Scenario: Feishu webhook dispatch
- **WHEN** a firing event is dispatched to a `webhook_feishu` channel
- **THEN** an HTTP POST SHALL be sent to the configured URL with a Feishu card JSON payload containing severity, rule name, current value, threshold, and action buttons for ACK

#### Scenario: WeCom webhook dispatch
- **WHEN** a firing event is dispatched to a `webhook_wecom` channel
- **THEN** an HTTP POST SHALL be sent with a WeCom markdown payload containing severity, rule name, current value, and threshold

#### Scenario: DingTalk webhook dispatch
- **WHEN** a firing event is dispatched to a `webhook_dingtalk` channel
- **THEN** an HTTP POST SHALL be sent with a DingTalk markdown payload containing severity, rule name, current value, and threshold

#### Scenario: Slack webhook dispatch
- **WHEN** a firing event is dispatched to a `webhook_slack` channel
- **THEN** an HTTP POST SHALL be sent with a Slack Block Kit JSON payload containing severity, rule name, current value, threshold, and a button for ACK

#### Scenario: Generic webhook dispatch
- **WHEN** a firing event is dispatched to a `webhook_generic` channel
- **THEN** an HTTP POST SHALL be sent with a JSON payload containing `alert_type`, `severity`, `rule_name`, `current_value`, `threshold`, `fired_at`, `event_id`

#### Scenario: WebSocket in-app dispatch
- **WHEN** a firing event is dispatched to a `websocket` channel
- **THEN** the event SHALL be broadcast to all connected WebSocket clients via `ConnectionManager.broadcast()` with a `type="alert_firing"` message

#### Scenario: Email dispatch
- **WHEN** a firing event is dispatched to an `email` channel
- **THEN** an HTML email SHALL be sent via SMTP to all configured recipients with subject `[Hecate Alert] {severity} - {rule_name}` and body containing alert details

#### Scenario: Webhook retry on failure
- **WHEN** a webhook dispatch returns HTTP 5xx or times out
- **THEN** the dispatcher SHALL retry up to 3 times with exponential backoff (1s, 2s, 4s) and log the final result

#### Scenario: Dispatch respects silence windows
- **WHEN** a firing event matches an active silence
- **THEN** the dispatcher SHALL skip notification for that event

### Requirement: Budget forecast with weighted moving average
The `cost_monthly_forecast` signal provider SHALL compute projected monthly cost using an exponentially-weighted moving average of daily costs over the last 7 days, extrapolated to the full month.

#### Scenario: Forecast with steady daily cost
- **WHEN** daily cost is $50/day for 7 days and the month has 30 days
- **THEN** the projected monthly cost SHALL be approximately $1500

#### Scenario: Forecast captures acceleration
- **WHEN** daily cost rises from $30 to $100 over 7 days
- **THEN** the projected monthly cost SHALL be higher than a simple linear projection from day-1 cost, reflecting the upward trend

#### Scenario: Forecast with no historical data
- **WHEN** the system has no cost data for the last 7 days
- **THEN** the projected monthly cost SHALL be 0.0

### Requirement: Quota soft-limit alert type
The system SHALL support `quota_soft_limit_reached` as an AlertType. This alert fires when a quota's `soft_limit` threshold is crossed during post-LLM usage recording.

#### Scenario: Soft limit crossed creates alert event
- **WHEN** a post-LLM recording causes quota usage to cross the soft_limit threshold for the first time in a period
- **THEN** an `AlertEventModel` is created with `alert_type="quota_soft_limit_reached"`, `current_value` set to the utilization percentage, and `severity="warning"`

#### Scenario: Soft limit alert notification dispatched
- **WHEN** the AlertEvaluator processes a `quota_soft_limit_reached` event
- **THEN** it SHALL dispatch notifications through the standard escalation policy, including the quota name and current usage in the message template
