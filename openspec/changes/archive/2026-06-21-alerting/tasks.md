## 1. Models Layer

- [x] 1.1 Create `src/hecate/models/alert.py` with `AlertType` enum (8 values), `AlertState` enum (4 values), `AlertSeverity` enum (3 values), `ChannelType` enum (7 values)
- [x] 1.2 Implement `AlertRuleModel(BaseModel)` with all fields: name, description, alert_type, threshold, window_minutes, for_minutes, severity, filters (JSON), enabled, escalation_policy_id, channel_ids (JSON), workspace_id
- [x] 1.3 Implement `AlertEventModel(BaseModel)` with fields: rule_id, state, current_value, fired_at, resolved_at, acked_at, acked_by, escalation_step, workspace_id
- [x] 1.4 Implement `AlertSilenceModel(BaseModel)` with fields: start_at, end_at, matchers (JSON), created_by, reason, workspace_id
- [x] 1.5 Implement `EscalationPolicyModel(BaseModel)` with fields: name, steps (JSON), repeat_interval_min, workspace_id
- [x] 1.6 Implement `NotificationChannelModel(BaseModel)` with fields: name, channel_type, config (JSON), enabled, workspace_id
- [x] 1.7 Implement Pydantic schemas: Create/Update/Read for each of the 5 models (rule, event, silence, escalation_policy, channel)

## 2. Migration

- [x] 2.1 Create Alembic migration creating 5 tables (alert_rules, alert_events, alert_silences, escalation_policies, notification_channels) with indexes on workspace_id, rule_id, state
- [x] 2.2 Seed a default escalation policy named "Standard Escalation" with steps [{delay_min: 0, channel_types: ["webhook_feishu", "websocket"]}, {delay_min: 15, channel_types: ["email"]}] and repeat_interval_min=60, idempotent check
- [x] 2.3 Verify migration chains from current head (e66673e35d7c) and applies cleanly

## 3. Signal Providers

- [x] 3.1 Create `src/hecate/services/signal_provider.py` with `SignalProvider` ABC: `async def get_value(self, rule: AlertRuleModel, window_minutes: int) -> float`
- [x] 3.2 Implement `ErrorRateProvider` — queries TraceModel for COUNT(status='error')/COUNT(*) in window with filters
- [x] 3.3 Implement `LatencyP95Provider` — queries TraceModel for 95th percentile of (end_time - start_time) in window
- [x] 3.4 Implement `LatencyTTFTProvider` — queries TraceModel for AVG(metadata->ttft_ms) WHERE type='GENERATION' in window
- [x] 3.5 Implement `TokenUsageProvider` — queries TraceModel for SUM(usage->total_tokens) WHERE type='GENERATION' in window
- [x] 3.6 Implement `CostDailyProvider` — delegates to CostService.get_cost_summary() for current day
- [x] 3.7 Implement `CostMonthlyForecastProvider` — computes EWMA of last 7 days daily costs, extrapolates to month length
- [x] 3.8 Implement `ToolFailureRateProvider` — queries TraceModel for COUNT(status='error' AND type='TOOL')/COUNT(type='TOOL') in window
- [x] 3.9 Implement `SuccessRateProvider` — queries TraceModel for COUNT(status='completed')/COUNT(*) in window
- [x] 3.10 Implement `SignalProviderRegistry` — maps AlertType → SignalProvider instance, with a `get_provider(alert_type)` method

## 4. Alert Service

- [x] 4.1 Create `src/hecate/services/alert_service.py` with `AlertService` class (inject AsyncSession)
- [x] 4.2 Implement rule CRUD: create_rule, list_rules, get_rule, update_rule, delete_rule (soft delete)
- [x] 4.3 Implement event queries: list_events (filter by state, rule_id, workspace), get_event, acknowledge_event (sets state=acked, acked_at, acked_by)
- [x] 4.4 Implement silence CRUD: create_silence, list_silences (with active filter), delete_silence
- [x] 4.5 Implement escalation policy CRUD: create_policy, list_policies, get_policy, update_policy, delete_policy
- [x] 4.6 Implement notification channel CRUD: create_channel, list_channels, update_channel, delete_channel
- [x] 4.7 Implement `is_silenced(event)` helper — queries active silences matching the event's rule and severity

## 5. Notification Dispatcher

- [x] 5.1 Create `src/hecate/services/notification_dispatcher.py` with `NotificationDispatcher` class
- [x] 5.2 Implement `FeishuTemplate` — formats AlertEvent into Feishu card JSON with severity, rule name, current value, threshold, ACK button
- [x] 5.3 Implement `WeComTemplate` — formats AlertEvent into WeCom markdown payload
- [x] 5.4 Implement `DingTalkTemplate` — formats AlertEvent into DingTalk markdown payload
- [x] 5.5 Implement `SlackTemplate` — formats AlertEvent into Slack Block Kit JSON with ACK button
- [x] 5.6 Implement `GenericWebhookTemplate` — formats AlertEvent into plain JSON payload
- [x] 5.7 Implement `EmailTemplate` — formats AlertEvent into HTML email with subject and body
- [x] 5.8 Implement `dispatch(event, channels)` — for each channel, select template by channel_type, render payload, send via httpx (webhook) or aiosmtplib (email) or ConnectionManager.broadcast (websocket)
- [x] 5.9 Implement webhook retry logic: 3 retries with exponential backoff (1s, 2s, 4s) on 5xx/timeout, log final result

## 6. Alert Evaluator

- [x] 6.1 Create `src/hecate/services/alert_evaluator.py` with `AlertEvaluator` class
- [x] 6.2 Implement advisory lock acquire/release using `pg_try_advisory_lock` / `pg_advisory_unlock` with a constant lock ID
- [x] 6.3 Implement `_evaluate_cycle()` — load enabled rules + active silences, for each rule: query signal provider, compare threshold, manage state transitions (pending → firing → resolved)
- [x] 6.4 Implement state transition logic: no event + condition met → create PENDING event; PENDING + for_minutes elapsed + still met → FIRING (set fired_at); PENDING/FIRING + not met → RESOLVED (set resolved_at)
- [x] 6.5 Implement escalation step progression: for FIRING events, compute current step from fired_at + delays, dispatch if step advanced; re-send step 0 if repeat_interval elapsed
- [x] 6.6 Implement silence check before dispatch: query active silences, skip dispatch if matched
- [x] 6.7 Implement background loop: asyncio task with configurable interval (default 60s), started/stopped in application lifespan
- [x] 6.8 Register evaluator start/stop in main.py lifespan alongside MonitoringService

## 7. TTFT Instrumentation

- [x] 7.1 Locate LLMWorker streaming call path in engine/workers/llm_worker.py
- [x] 7.2 Add TTFT measurement: timestamp first chunk arrival, compute ttft_ms = (first_chunk_time - start_time) * 1000
- [x] 7.3 Write ttft_ms into TraceModel metadata_["ttft_ms"] and total_latency_ms into metadata_["total_latency_ms"] for GENERATION spans
- [x] 7.4 Ensure non-streaming calls set ttft_ms = total_latency_ms (first byte = last byte)

## 8. API Layer

- [x] 8.1 Create `src/hecate/api/management/alerts.py` with 5 routers: rules_router, events_router, silences_router, channels_router, escalation_policies_router
- [x] 8.2 Implement rules router: POST/GET/PUT/DELETE /api/alerts/rules with AuthContext + AsyncSession dependencies
- [x] 8.3 Implement events router: GET /api/alerts/events (with state, rule_id filters), POST /api/alerts/events/{id}/ack
- [x] 8.4 Implement silences router: POST/GET/DELETE /api/alerts/silences (with active filter)
- [x] 8.5 Implement channels router: POST/GET/PUT/DELETE /api/alerts/channels, POST /api/alerts/channels/{id}/test (dispatch test notification)
- [x] 8.6 Implement escalation_policies router: POST/GET/PUT/DELETE /api/alerts/escalation-policies
- [x] 8.7 Register all 5 routers in main.py with correct imports and include_router calls

## 9. Configuration

- [x] 9.1 Add alert settings to core/config.py: ALERT_EVAL_INTERVAL (int, default 60), ALERT_SMTP_HOST, ALERT_SMTP_PORT, ALERT_SMTP_USER, ALERT_SMTP_PASSWORD, ALERT_SMTP_FROM, ALERT_ENABLED (bool, default True)
- [x] 9.2 Add aiosmtplib to pyproject.toml [observability] optional dependency group

## 10. Tests

- [x] 10.1 Create `tests/test_services/test_alert_service.py` — test model creation, rule CRUD, event queries, ACK, silence CRUD, escalation policy CRUD, channel CRUD
- [x] 10.2 Test signal providers: mock TraceModel data, verify each provider returns correct aggregated value (error_rate, latency_p95, ttft, token_usage, cost_daily, cost_forecast, tool_failure_rate, success_rate)
- [x] 10.3 Test evaluator state transitions: condition met → PENDING → FIRING (after for_minutes) → RESOLVED (condition clears), ACK stops escalation
- [x] 10.4 Test evaluator advisory lock: lock unavailable → cycle skipped
- [x] 10.5 Test escalation step progression: step 0 at fire time, step 1 after delay, repeat after repeat_interval
- [x] 10.6 Test silence suppression: silenced event → no dispatch; expired silence → dispatch resumes
- [x] 10.7 Test notification dispatcher: each template renders correct payload format (Feishu card, WeCom markdown, DingTalk markdown, Slack Block Kit, generic JSON, email HTML)
- [x] 10.8 Test webhook retry: mock 5xx response → 3 retries with backoff; mock success → no retry
- [x] 10.9 Test budget forecast: steady cost → linear projection; accelerating cost → EWMA > linear; no data → 0.0

## 11. Verification

- [x] 11.1 Run ruff check src/hecate/ tests/ — zero errors
- [x] 11.2 Run ruff format --check src/ tests/ — zero changes needed
- [x] 11.3 Run mypy src/ — zero errors
- [x] 11.4 Run pytest tests/ -q — all tests pass
