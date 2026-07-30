## ADDED Requirements — 新增需求

### Requirement: AlertRuleModel ORM model — 需求：AlertRuleModel ORM 模型
The system SHALL define `AlertRuleModel(BaseModel)` in `models/alert.py` with fields: `name` (String 255), `description` (String, nullable), `alert_type` (String, one of AlertType enum), `threshold` (Float), `window_minutes` (Integer, evaluation window), `for_minutes` (Integer, sustained duration before firing), `severity` (String, one of AlertSeverity enum), `filters` (JSON, optional scope filters: agent_id, model), `enabled` (Boolean, default True), `escalation_policy_id` (UUID FK, nullable), `channel_ids` (JSON, list of NotificationChannel UUIDs), `workspace_id` (UUID, default zero UUID).

系统应在 `models/alert.py` 中定义 `AlertRuleModel(BaseModel)`，包含字段：`name`（String 255）、`description`（String，可空）、`alert_type`（String，AlertType 枚举之一）、`threshold`（Float）、`window_minutes`（Integer，评估窗口）、`for_minutes`（Integer，触发前持续时长）、`severity`（String，AlertSeverity 枚举之一）、`filters`（JSON，可选范围过滤器：agent_id、model）、`enabled`（Boolean，默认 True）、`escalation_policy_id`（UUID FK，可空）、`channel_ids`（JSON，NotificationChannel UUID 列表）、`workspace_id`（UUID，默认零 UUID）。

#### Scenario: Create a rule with all fields — 场景：创建包含所有字段的规则
- **WHEN** an `AlertRuleModel` is created with `name="High Error Rate"`, `alert_type="error_rate"`, `threshold=0.05`, `window_minutes=5`, `for_minutes=2`, `severity="critical"`
- **THEN** the record is persisted with `enabled=True` and the specified values

- **当**创建 `AlertRuleModel` 时设置 `name="High Error Rate"`、`alert_type="error_rate"`、`threshold=0.05`、`window_minutes=5`、`for_minutes=2`、`severity="critical"`
- **则**记录以 `enabled=True` 和指定值持久化

#### Scenario: Create a rule with scope filter — 场景：创建带范围过滤器的规则
- **WHEN** an `AlertRuleModel` is created with `filters={"agent_id": "<uuid>", "model": "gpt-4o"}`
- **THEN** the evaluator SHALL only evaluate traces matching those filters when assessing this rule

- **当**创建 `AlertRuleModel` 时设置 `filters={"agent_id": "<uuid>", "model": "gpt-4o"}`
- **则**评估器在评估此规则时仅评估匹配这些过滤器的追踪

#### Scenario: Disabled rule is not evaluated — 场景：禁用的规则不被评估
- **WHEN** a rule has `enabled=False`
- **THEN** the evaluator SHALL skip it during evaluation cycles

- **当**规则设置 `enabled=False`
- **则**评估器在评估周期中跳过该规则

### Requirement: AlertEventModel ORM model — 需求：AlertEventModel ORM 模型
The system SHALL define `AlertEventModel(BaseModel)` in `models/alert.py` with fields: `rule_id` (UUID FK to AlertRuleModel), `state` (String, one of AlertState enum: pending, firing, resolved, acked), `current_value` (Float, the metric value that triggered the alert), `fired_at` (DateTime, nullable), `resolved_at` (DateTime, nullable), `acked_at` (DateTime, nullable), `acked_by` (UUID, nullable), `escalation_step` (Integer, default 0), `workspace_id` (UUID).

系统应在 `models/alert.py` 中定义 `AlertEventModel(BaseModel)`，包含字段：`rule_id`（UUID FK 到 AlertRuleModel）、`state`（String，AlertState 枚举之一：pending、firing、resolved、acked）、`current_value`（Float，触发告警的指标值）、`fired_at`（DateTime，可空）、`resolved_at`（DateTime，可空）、`acked_at`（DateTime，可空）、`acked_by`（UUID，可空）、`escalation_step`（Integer，默认 0）、`workspace_id`（UUID）。

#### Scenario: Event created in pending state — 场景：事件在待定状态创建
- **WHEN** a rule's threshold is first breached
- **THEN** an `AlertEventModel` is created with `state="pending"`, `current_value` set to the triggering value, `escalation_step=0`

- **当**规则阈值首次被突破
- **则**创建 `AlertEventModel`，`state="pending"`，`current_value` 设置为触发值，`escalation_step=0`

#### Scenario: Event transitions to firing — 场景：事件转换为触发中
- **WHEN** a pending event's `for_minutes` duration has elapsed and the condition still holds
- **THEN** the event's `state` SHALL be updated to `"firing"` and `fired_at` SHALL be set to the current timestamp

- **当**待定事件的 `for_minutes` 持续时间已过且条件仍成立
- **则**事件的 `state` 应更新为 `"firing"`，`fired_at` 应设置为当前时间戳

#### Scenario: Event transitions to resolved — 场景：事件转换为已解决
- **WHEN** a firing or pending event's condition no longer holds
- **THEN** the event's `state` SHALL be updated to `"resolved"` and `resolved_at` SHALL be set to the current timestamp

- **当**触发中或待定事件的条件不再成立
- **则**事件的 `state` 应更新为 `"resolved"`，`resolved_at` 应设置为当前时间戳

#### Scenario: Event acknowledged manually — 场景：事件手动确认
- **WHEN** a user calls the ACK endpoint on a firing event
- **THEN** the event's `state` SHALL be updated to `"acked"`, `acked_at` set to current timestamp, `acked_by` set to the user's ID, and escalation SHALL stop

- **当**用户对触发中事件调用 ACK 端点
- **则**事件的 `state` 应更新为 `"acked"`，`acked_at` 设置为当前时间戳，`acked_by` 设置为用户 ID，且升级应停止

### Requirement: AlertSilenceModel ORM model — 需求：AlertSilenceModel ORM 模型
The system SHALL define `AlertSilenceModel(BaseModel)` in `models/alert.py` with fields: `start_at` (DateTime), `end_at` (DateTime), `matchers` (JSON: optional keys `rule_ids` list, `severity` list), `created_by` (UUID, nullable), `reason` (String, nullable), `workspace_id` (UUID).

系统应在 `models/alert.py` 中定义 `AlertSilenceModel(BaseModel)`，包含字段：`start_at`（DateTime）、`end_at`（DateTime）、`matchers`（JSON：可选键 `rule_ids` 列表、`severity` 列表）、`created_by`（UUID，可空）、`reason`（String，可空）、`workspace_id`（UUID）。

#### Scenario: Silence suppresses matching notifications — 场景：静默抑制匹配的通知
- **WHEN** a firing event matches a silence's matchers and the current time is within `[start_at, end_at]`
- **THEN** no notification SHALL be dispatched for that event

- **当**触发中事件匹配静默的匹配器且当前时间在 `[start_at, end_at]` 范围内
- **则**不应为该事件发送通知

#### Scenario: Silence auto-expires — 场景：静默自动过期
- **WHEN** the current time exceeds `end_at`
- **THEN** the silence SHALL no longer suppress notifications, even if matchers would match

- **当**当前时间超过 `end_at`
- **则**静默不再抑制通知，即使匹配器匹配

#### Scenario: Silence scoped to specific rules — 场景：静默限定到特定规则
- **WHEN** a silence has `matchers={"rule_ids": ["<uuid1>", "<uuid2>"]}`
- **THEN** only events from those rules SHALL be silenced; events from other rules SHALL still notify

- **当**静默设置 `matchers={"rule_ids": ["<uuid1>", "<uuid2>"]}`
- **则**仅静默来自这些规则的事件；其他规则的事件仍应通知

### Requirement: EscalationPolicyModel ORM model — 需求：EscalationPolicyModel ORM 模型
The system SHALL define `EscalationPolicyModel(BaseModel)` in `models/alert.py` with fields: `name` (String 255), `steps` (JSON array of `{delay_min: int, channel_types: [str]}`), `repeat_interval_min` (Integer, nullable, repeat cadence for re-sending step 0), `workspace_id` (UUID).

系统应在 `models/alert.py` 中定义 `EscalationPolicyModel(BaseModel)`，包含字段：`name`（String 255）、`steps`（`{delay_min: int, channel_types: [str]}` 的 JSON 数组）、`repeat_interval_min`（Integer，可空，重新发送步骤 0 的重复节奏）、`workspace_id`（UUID）。

#### Scenario: Default escalation policy seeded by migration — 场景：迁移时创建默认升级策略
- **WHEN** the migration is applied
- **THEN** a default policy named `"Standard Escalation"` SHALL exist with steps `[{delay_min: 0, channel_types: ["webhook_feishu", "websocket"]}, {delay_min: 15, channel_types: ["email"]}]` and `repeat_interval_min=60`

- **当**应用迁移时
- **则**应存在名为 `"Standard Escalation"` 的默认策略，包含步骤 `[{delay_min: 0, channel_types: ["webhook_feishu", "websocket"]}, {delay_min: 15, channel_types: ["email"]}]` 和 `repeat_interval_min=60`

#### Scenario: Escalation progresses through steps — 场景：升级按步骤推进
- **WHEN** a firing event has `fired_at` 20 minutes ago and the policy has steps at delay 0 and 15
- **THEN** the evaluator SHALL have dispatched step 0 (at fire time) and step 1 (at +15 min), and `escalation_step` SHALL be 1

- **当**触发中事件的 `fired_at` 在 20 分钟前，且策略在延迟 0 和 15 有步骤
- **则**评估器应已发送步骤 0（触发时）和步骤 1（+15 分钟），且 `escalation_step` 应为 1

#### Scenario: Escalation stops on ACK — 场景：ACK 时停止升级
- **WHEN** an event is ACKED
- **THEN** no further escalation steps SHALL be dispatched, even if `repeat_interval_min` has elapsed

- **当**事件被 ACK
- **则**不应再发送升级步骤，即使 `repeat_interval_min` 已过

#### Scenario: Repeat interval re-sends step 0 — 场景：重复间隔重新发送步骤 0
- **WHEN** a firing event is not ACKED and `repeat_interval_min` has elapsed since the last dispatch
- **THEN** step 0 channels SHALL be re-dispatched

- **当**触发中事件未被 ACK 且自上次发送后 `repeat_interval_min` 已过
- **则**应重新发送步骤 0 的通道

### Requirement: NotificationChannelModel ORM model — 需求：NotificationChannelModel ORM 模型
The system SHALL define `NotificationChannelModel(BaseModel)` in `models/alert.py` with fields: `name` (String 255), `channel_type` (String, one of ChannelType enum), `config` (JSON: type-specific config, e.g., `url` for webhooks, `recipients` for email), `enabled` (Boolean, default True), `workspace_id` (UUID).

系统应在 `models/alert.py` 中定义 `NotificationChannelModel(BaseModel)`，包含字段：`name`（String 255）、`channel_type`（String，ChannelType 枚举之一）、`config`（JSON：特定类型的配置，例如 webhook 的 `url`、邮件的 `recipients`）、`enabled`（Boolean，默认 True）、`workspace_id`（UUID）。

#### Scenario: Create a Feishu webhook channel — 场景：创建飞书 webhook 通道
- **WHEN** a `NotificationChannelModel` is created with `channel_type="webhook_feishu"`, `config={"url": "https://open.feishu.cn/open-apis/bot/v2/hook/xxx"}`
- **THEN** the channel is available for assignment to alert rules

- **当**创建 `NotificationChannelModel`，设置 `channel_type="webhook_feishu"`、`config={"url": "https://open.feishu.cn/open-apis/bot/v2/hook/xxx"}`
- **则**该通道可用于分配给告警规则

#### Scenario: Create an email channel — 场景：创建邮件通道
- **WHEN** a `NotificationChannelModel` is created with `channel_type="email"`, `config={"recipients": ["admin@company.com"]}`
- **THEN** the channel SHALL dispatch emails to the specified recipients when notified

- **当**创建 `NotificationChannelModel`，设置 `channel_type="email"`、`config={"recipients": ["admin@company.com"]}`
- **则**该通道在通知时应向指定收件人发送邮件

#### Scenario: Disabled channel is skipped — 场景：禁用的通道被跳过
- **WHEN** a channel has `enabled=False`
- **THEN** the dispatcher SHALL skip it during notification dispatch

- **当**通道设置 `enabled=False`
- **则**调度器在通知发送时应跳过该通道

### Requirement: Alert rule CRUD API — 需求：告警规则 CRUD API
The system SHALL expose REST endpoints for alert rule management under `/api/alerts/rules`.

系统应在 `/api/alerts/rules` 下公开用于告警规则管理的 REST 端点。

#### Scenario: Create a rule — 场景：创建规则
- **WHEN** `POST /api/alerts/rules` is called with a valid rule schema
- **THEN** a new rule is created and returned with status 201

- **当**使用有效规则模式调用 `POST /api/alerts/rules`
- **则**创建新规则并以状态 201 返回

#### Scenario: List rules — 场景：列出规则
- **WHEN** `GET /api/alerts/rules` is called
- **THEN** a paginated list of rules for the current workspace is returned, ordered by `created_at` descending

- **当**调用 `GET /api/alerts/rules`
- **则**返回当前工作空间的分页规则列表，按 `created_at` 降序排列

#### Scenario: Update a rule — 场景：更新规则
- **WHEN** `PUT /api/alerts/rules/{id}` is called with updated `threshold`
- **THEN** the rule is updated and returned

- **当**使用更新后的 `threshold` 调用 `PUT /api/alerts/rules/{id}`
- **则**规则被更新并返回

#### Scenario: Delete a rule — 场景：删除规则
- **WHEN** `DELETE /api/alerts/rules/{id}` is called
- **THEN** the rule is soft-deleted and status 204 is returned

- **当**调用 `DELETE /api/alerts/rules/{id}`
- **则**规则被软删除，返回状态 204

### Requirement: Alert event query and ACK API — 需求：告警事件查询和 ACK API
The system SHALL expose REST endpoints for alert event management under `/api/alerts/events`.

系统应在 `/api/alerts/events` 下公开用于告警事件管理的 REST 端点。

#### Scenario: List events filtered by state — 场景：按状态过滤列出事件
- **WHEN** `GET /api/alerts/events?state=firing` is called
- **THEN** only events with `state="firing"` are returned, ordered by `fired_at` descending

- **当**调用 `GET /api/alerts/events?state=firing`
- **则**仅返回 `state="firing"` 的事件，按 `fired_at` 降序排列

#### Scenario: List events filtered by rule — 场景：按规则过滤列出事件
- **WHEN** `GET /api/alerts/events?rule_id=<uuid>` is called
- **THEN** only events for the specified rule are returned

- **当**调用 `GET /api/alerts/events?rule_id=<uuid>`
- **则**仅返回指定规则的事件

#### Scenario: Acknowledge an event — 场景：确认事件
- **WHEN** `POST /api/alerts/events/{id}/ack` is called
- **THEN** the event's `state` is updated to `"acked"`, `acked_at` and `acked_by` are set, and status 200 is returned

- **当**调用 `POST /api/alerts/events/{id}/ack`
- **则**事件的 `state` 更新为 `"acked"`，设置 `acked_at` 和 `acked_by`，返回状态 200

### Requirement: Silence window CRUD API — 需求：静默窗口 CRUD API
The system SHALL expose REST endpoints for silence management under `/api/alerts/silences`.

系统应在 `/api/alerts/silences` 下公开用于静默管理的 REST 端点。

#### Scenario: Create a silence window — 场景：创建静默窗口
- **WHEN** `POST /api/alerts/silences` is called with `start_at`, `end_at`, `matchers`, and `reason`
- **THEN** a new silence record is created and returned with status 201

- **当**使用 `start_at`、`end_at`、`matchers` 和 `reason` 调用 `POST /api/alerts/silences`
- **则**创建新的静默记录并以状态 201 返回

#### Scenario: List active silences — 场景：列出活跃静默
- **WHEN** `GET /api/alerts/silences?active=true` is called
- **THEN** only silences where `start_at <= now() <= end_at` are returned

- **当**调用 `GET /api/alerts/silences?active=true`
- **则**仅返回 `start_at <= now() <= end_at` 的静默

#### Scenario: Delete a silence — 场景：删除静默
- **WHEN** `DELETE /api/alerts/silences/{id}` is called
- **THEN** the silence is deleted and status 204 is returned

- **当**调用 `DELETE /api/alerts/silences/{id}`
- **则**静默被删除，返回状态 204

### Requirement: Notification channel CRUD API — 需求：通知通道 CRUD API
The system SHALL expose REST endpoints for notification channel management under `/api/alerts/channels`.

系统应在 `/api/alerts/channels` 下公开用于通知通道管理的 REST 端点。

#### Scenario: Create a channel — 场景：创建通道
- **WHEN** `POST /api/alerts/channels` is called with a valid channel type and config
- **THEN** a new channel is created and returned with status 201

- **当**使用有效的通道类型和配置调用 `POST /api/alerts/channels`
- **则**创建新通道并以状态 201 返回

#### Scenario: List channels — 场景：列出通道
- **WHEN** `GET /api/alerts/channels` is called
- **THEN** all channels for the current workspace are returned

- **当**调用 `GET /api/alerts/channels`
- **则**返回当前工作空间的所有通道

#### Scenario: Test a channel — 场景：测试通道
- **WHEN** `POST /api/alerts/channels/{id}/test` is called
- **THEN** a test notification SHALL be dispatched to the channel and the result (success/failure) SHALL be returned

- **当**调用 `POST /api/alerts/channels/{id}/test`
- **则**应向通道发送测试通知并返回结果（成功/失败）

### Requirement: Escalation policy CRUD API — 需求：升级策略 CRUD API
The system SHALL expose REST endpoints for escalation policy management under `/api/alerts/escalation-policies`.

系统应在 `/api/alerts/escalation-policies` 下公开用于升级策略管理的 REST 端点。

#### Scenario: Create a custom policy — 场景：创建自定义策略
- **WHEN** `POST /api/alerts/escalation-policies` is called with steps and repeat interval
- **THEN** a new policy is created and returned with status 201

- **当**使用步骤和重复间隔调用 `POST /api/alerts/escalation-policies`
- **则**创建新策略并以状态 201 返回

#### Scenario: List policies — 场景：列出策略
- **WHEN** `GET /api/alerts/escalation-policies` is called
- **THEN** all policies for the current workspace are returned, including the default seeded policy

- **当**调用 `GET /api/alerts/escalation-policies`
- **则**返回当前工作空间的所有策略，包括默认种子策略

### Requirement: Alert evaluator with advisory lock — 需求：带建议锁的告警评估器
The system SHALL run an `AlertEvaluator` asyncio background task that acquires a PostgreSQL session-level advisory lock, loads enabled rules and active silences, evaluates each rule's signal against its threshold, manages event state transitions, and dispatches notifications. The evaluator SHALL run at a configurable interval (default 60 seconds).

系统应运行 `AlertEvaluator` asyncio 后台任务，该任务获取 PostgreSQL 会话级建议锁，加载启用的规则和活跃的静默，评估每个规则的信号与其阈值，管理事件状态转换，并发送通知。评估器应以可配置的间隔运行（默认 60 秒）。

#### Scenario: Evaluator acquires advisory lock — 场景：评估器获取建议锁
- **WHEN** the evaluator starts an evaluation cycle
- **THEN** it SHALL call `pg_try_advisory_lock(<constant_id>)` and proceed only if the lock is acquired

- **当**评估器开始评估周期
- **则**应调用 `pg_try_advisory_lock(<constant_id>)`，仅在获取锁后继续

#### Scenario: Evaluator skips when lock unavailable — 场景：锁不可用时评估器跳过
- **WHEN** another node holds the advisory lock
- **THEN** the evaluator SHALL skip the cycle and wait for the next interval

- **当**另一个节点持有建议锁
- **则**评估器应跳过该周期并等待下一个间隔

#### Scenario: Evaluator evaluates error_rate rule — 场景：评估器评估 error_rate 规则
- **WHEN** a rule with `alert_type="error_rate"`, `threshold=0.05`, `window_minutes=5` is evaluated
- **THEN** the evaluator SHALL query `COUNT(status='error') / COUNT(*)` from TraceModel in the last 5 minutes (with rule filters applied) and compare against 0.05

- **当**评估 `alert_type="error_rate"`、`threshold=0.05`、`window_minutes=5` 的规则
- **则**评估器应从 TraceModel 查询过去 5 分钟内 `COUNT(status='error') / COUNT(*)`（应用规则过滤器）并与 0.05 比较

#### Scenario: Evaluator evaluates cost_monthly_forecast rule — 场景：评估器评估 cost_monthly_forecast 规则
- **WHEN** a rule with `alert_type="cost_monthly_forecast"`, `threshold=1000.0` is evaluated
- **THEN** the evaluator SHALL compute the projected monthly cost using weighted moving average of recent daily costs and compare against the threshold

- **当**评估 `alert_type="cost_monthly_forecast"`、`threshold=1000.0` 的规则
- **则**评估器应使用近期每日成本的加权移动平均计算预计月度成本并与阈值比较

### Requirement: SignalProvider registry for 8 alert types — 需求：8 种告警类型的 SignalProvider 注册表
The system SHALL implement a `SignalProvider` registry with one provider per `AlertType`. Each provider queries `TraceModel` or `CostService` for the relevant metric within the specified window and returns the current value.

系统应实现 `SignalProvider` 注册表，每个 `AlertType` 对应一个提供者。每个提供者在指定窗口内查询 `TraceModel` 或 `CostService` 获取相关指标并返回当前值。

#### Scenario: Error rate signal provider — 场景：错误率信号提供者
- **WHEN** the `error_rate` provider is queried with `window_minutes=5` and filters `{"agent_id": "<uuid>"}`
- **THEN** it SHALL return `COUNT(status='error') / COUNT(*)` from TraceModel where `start_time >= now() - 5 minutes` and `agent_id` matches

- **当**使用 `window_minutes=5` 和过滤器 `{"agent_id": "<uuid>"}` 查询 `error_rate` 提供者
- **则**应返回 TraceModel 中 `start_time >= now() - 5 minutes` 且 `agent_id` 匹配的 `COUNT(status='error') / COUNT(*)`

#### Scenario: Latency P95 signal provider — 场景：延迟 P95 信号提供者
- **WHEN** the `latency_p95` provider is queried with `window_minutes=10`
- **THEN** it SHALL return the 95th percentile of `(end_time - start_time)` in milliseconds for completed traces in the window

- **当**使用 `window_minutes=10` 查询 `latency_p95` 提供者
- **则**应返回窗口内已完成追踪的 `(end_time - start_time)` 毫秒数第 95 百分位

#### Scenario: TTFT signal provider — 场景：TTFT 信号提供者
- **WHEN** the `latency_ttft` provider is queried with `window_minutes=10`
- **THEN** it SHALL return the average `metadata->ttft_ms` from GENERATION-type traces in the window

- **当**使用 `window_minutes=10` 查询 `latency_ttft` 提供者
- **则**应返回窗口内 GENERATION 类型追踪的平均 `metadata->ttft_ms`

#### Scenario: Token usage signal provider — 场景：Token 使用量信号提供者
- **WHEN** the `token_usage` provider is queried with `window_minutes=60`
- **THEN** it SHALL return `SUM(usage->total_tokens)` from GENERATION traces in the window

- **当**使用 `window_minutes=60` 查询 `token_usage` 提供者
- **则**应返回窗口内 GENERATION 追踪的 `SUM(usage->total_tokens)`

#### Scenario: Cost daily signal provider — 场景：每日成本信号提供者
- **WHEN** the `cost_daily` provider is queried
- **THEN** it SHALL delegate to `CostService.get_cost_summary()` for the current day and return `total_cost`

- **当**查询 `cost_daily` 提供者
- **则**应委托 `CostService.get_cost_summary()` 获取当日数据并返回 `total_cost`

#### Scenario: Cost monthly forecast signal provider — 场景：月度成本预测信号提供者
- **WHEN** the `cost_monthly_forecast` provider is queried
- **THEN** it SHALL compute `recent_avg_daily_cost * days_in_month` using exponentially-weighted moving average of the last 7 days and return the projected value

- **当**查询 `cost_monthly_forecast` 提供者
- **则**应使用过去 7 天的指数加权移动平均计算 `recent_avg_daily_cost * days_in_month` 并返回预测值

#### Scenario: Tool failure rate signal provider — 场景：工具失败率信号提供者
- **WHEN** the `tool_failure_rate` provider is queried with `window_minutes=10`
- **THEN** it SHALL return `COUNT(status='error' AND type='TOOL') / COUNT(type='TOOL')` from TraceModel in the window

- **当**使用 `window_minutes=10` 查询 `tool_failure_rate` 提供者
- **则**应返回窗口内 TraceModel 中 `COUNT(status='error' AND type='TOOL') / COUNT(type='TOOL')`

#### Scenario: Success rate signal provider — 场景：成功率信号提供者
- **WHEN** the `success_rate` provider is queried with `window_minutes=5`
- **THEN** it SHALL return `COUNT(status='completed') / COUNT(*)` from TraceModel in the window

- **当**使用 `window_minutes=5` 查询 `success_rate` 提供者
- **则**应返回窗口内 TraceModel 中 `COUNT(status='completed') / COUNT(*)`

### Requirement: NotificationDispatcher with built-in templates — 需求：带内置模板的 NotificationDispatcher
The system SHALL implement a `NotificationDispatcher` that routes firing alert events through the appropriate escalation policy steps to the configured notification channels. Each channel type SHALL use a built-in message template that formats the alert into the platform's native format.

系统应实现 `NotificationDispatcher`，将触发中的告警事件通过适当的升级策略步骤路由到配置的通知通道。每个通道类型应使用内置消息模板，将告警格式化为平台的原生格式。

#### Scenario: Feishu webhook dispatch — 场景：飞书 webhook 发送
- **WHEN** a firing event is dispatched to a `webhook_feishu` channel
- **THEN** an HTTP POST SHALL be sent to the configured URL with a Feishu card JSON payload containing severity, rule name, current value, threshold, and action buttons for ACK

- **当**触发中事件被发送到 `webhook_feishu` 通道
- **则**应向配置的 URL 发送 HTTP POST，包含飞书卡片 JSON 负载，包含严重级别、规则名称、当前值、阈值和 ACK 操作按钮

#### Scenario: WeCom webhook dispatch — 场景：企业微信 webhook 发送
- **WHEN** a firing event is dispatched to a `webhook_wecom` channel
- **THEN** an HTTP POST SHALL be sent with a WeCom markdown payload containing severity, rule name, current value, and threshold

- **当**触发中事件被发送到 `webhook_wecom` 通道
- **则**应发送 HTTP POST，包含企业微信 markdown 负载，包含严重级别、规则名称、当前值和阈值

#### Scenario: DingTalk webhook dispatch — 场景：钉钉 webhook 发送
- **WHEN** a firing event is dispatched to a `webhook_dingtalk` channel
- **THEN** an HTTP POST SHALL be sent with a DingTalk markdown payload containing severity, rule name, current value, and threshold

- **当**触发中事件被发送到 `webhook_dingtalk` 通道
- **则**应发送 HTTP POST，包含钉钉 markdown 负载，包含严重级别、规则名称、当前值和阈值

#### Scenario: Slack webhook dispatch — 场景：Slack webhook 发送
- **WHEN** a firing event is dispatched to a `webhook_slack` channel
- **THEN** an HTTP POST SHALL be sent with a Slack Block Kit JSON payload containing severity, rule name, current value, threshold, and a button for ACK

- **当**触发中事件被发送到 `webhook_slack` 通道
- **则**应发送 HTTP POST，包含 Slack Block Kit JSON 负载，包含严重级别、规则名称、当前值、阈值和 ACK 按钮

#### Scenario: Generic webhook dispatch — 场景：通用 webhook 发送
- **WHEN** a firing event is dispatched to a `webhook_generic` channel
- **THEN** an HTTP POST SHALL be sent with a JSON payload containing `alert_type`, `severity`, `rule_name`, `current_value`, `threshold`, `fired_at`, `event_id`

- **当**触发中事件被发送到 `webhook_generic` 通道
- **则**应发送 HTTP POST，包含 JSON 负载，包含 `alert_type`、`severity`、`rule_name`、`current_value`、`threshold`、`fired_at`、`event_id`

#### Scenario: WebSocket in-app dispatch — 场景：WebSocket 应用内发送
- **WHEN** a firing event is dispatched to a `websocket` channel
- **THEN** the event SHALL be broadcast to all connected WebSocket clients via `ConnectionManager.broadcast()` with a `type="alert_firing"` message

- **当**触发中事件被发送到 `websocket` 通道
- **则**应通过 `ConnectionManager.broadcast()` 向所有已连接的 WebSocket 客户端广播 `type="alert_firing"` 消息

#### Scenario: Email dispatch — 场景：邮件发送
- **WHEN** a firing event is dispatched to an `email` channel
- **THEN** an HTML email SHALL be sent via SMTP to all configured recipients with subject `[Hecate Alert] {severity} - {rule_name}` and body containing alert details

- **当**触发中事件被发送到 `email` 通道
- **则**应通过 SMTP 向所有配置的收件人发送 HTML 邮件，主题为 `[Hecate Alert] {severity} - {rule_name}`，正文包含告警详情

#### Scenario: Webhook retry on failure — 场景：失败时 webhook 重试
- **WHEN** a webhook dispatch returns HTTP 5xx or times out
- **THEN** the dispatcher SHALL retry up to 3 times with exponential backoff (1s, 2s, 4s) and log the final result

- **当** webhook 发送返回 HTTP 5xx 或超时
- **则**调度器应最多重试 3 次，使用指数退避（1s、2s、4s）并记录最终结果

#### Scenario: Dispatch respects silence windows — 场景：发送尊重静默窗口
- **WHEN** a firing event matches an active silence
- **THEN** the dispatcher SHALL skip notification for that event

- **当**触发中事件匹配活跃静默
- **则**调度器应跳过该事件的通知

### Requirement: Budget forecast with weighted moving average — 需求：带加权移动平均的预算预测
The `cost_monthly_forecast` signal provider SHALL compute projected monthly cost using an exponentially-weighted moving average of daily costs over the last 7 days, extrapolated to the full month.

`cost_monthly_forecast` 信号提供者应使用过去 7 天每日成本的指数加权移动平均计算预计月度成本，并外推到整月。

#### Scenario: Forecast with steady daily cost — 场景：稳定每日成本的预测
- **WHEN** daily cost is $50/day for 7 days and the month has 30 days
- **THEN** the projected monthly cost SHALL be approximately $1500

- **当**每日成本为 50 美元/天持续 7 天且当月有 30 天
- **则**预计月度成本应约为 1500 美元

#### Scenario: Forecast captures acceleration — 场景：预测捕捉增长趋势
- **WHEN** daily cost rises from $30 to $100 over 7 days
- **THEN** the projected monthly cost SHALL be higher than a simple linear projection from day-1 cost, reflecting the upward trend

- **当**每日成本在 7 天内从 30 美元上升到 100 美元
- **则**预计月度成本应高于基于第一天成本的简单线性预测，反映上升趋势

#### Scenario: Forecast with no historical data — 场景：无历史数据的预测
- **WHEN** the system has no cost data for the last 7 days
- **THEN** the projected monthly cost SHALL be 0.0

- **当**系统在过去 7 天没有成本数据
- **则**预计月度成本应为 0.0
