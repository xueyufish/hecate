## Context

Hecate's observability stack currently provides tracing (8.1), real-time monitoring (8.2), cost dashboard (8.3), and audit logs (8.7), but operators must manually check dashboards to discover problems. Competitive analysis across Dify, Coze, LangFuse, LiteLLM, Grafana/Prometheus, and Datadog shows that alerting is a fundamental expectation for enterprise platforms.

The existing infrastructure provides a strong foundation: `TraceModel` records every span with status, latency, token usage, and model metadata; `CostService` aggregates cost from traces; `MonitoringService` demonstrates the asyncio background-task pattern with WebSocket push; `ScheduleManager` shows advisory-lock usage for multi-node safety. This change builds alerting on top of these existing components.

Key constraints: PostgreSQL is the primary datastore (advisory locks available); no Redis or Celery; the `[scheduling]` dependency group is optional and must not be required; all code follows the models → services → api layering with zero engine-layer imports from services/api.

## Goals / Non-Goals

**Goals:**
- 8 alert types covering the full catalog description (error rate, latency p95, TTFT, token usage, daily cost, monthly cost forecast, tool failure rate, success rate).
- Grafana-standard alert lifecycle: PENDING → FIRING → RESOLVED, with manual ACK.
- Three-severity escalation: CRITICAL (webhook + SMS), WARNING (webhook + in-app), INFO (in-app + email).
- Built-in message templates for Feishu (card), WeCom (markdown), DingTalk (markdown), Slack (Block Kit), and email (HTML).
- Maintenance-window silencing to suppress notifications during planned downtime.
- Multi-node safe evaluation via PostgreSQL advisory lock.
- Per-workspace rule isolation with optional scope filters (agent_id, model).

**Non-Goals:**
- Anomaly detection (statistical baseline, z-score, EWMA deviation) — deferred to a future 8.6a feature. v1 is threshold + forecast only.
- Expression-based rule engine (PromQL or custom DSL) — rules use fixed AlertType enum with structured parameters.
- SMS and phone call channels — v1 provides webhook, WebSocket, and email. SMS/phone require a telephony provider integration (Twilio/Alibaba SMS) and are deferred.
- Alert correlation or deduplication across rules — each rule evaluates independently.
- Real-time streaming evaluation — v1 uses periodic polling (60s default). Streaming is a future optimization.

## Decisions

### D1: Fixed AlertType enum over PromQL expression engine

Rules use a fixed enum (`AlertType`) with structured parameters (`threshold`, `window_minutes`, `for_minutes`, `filters`) rather than a query-language expression like PromQL.

**Alternatives considered:**
- **PromQL engine** (Dify/Grafana pattern): Extremely flexible, but requires implementing a query parser, expression evaluator, and time-series database adapter. Unjustified complexity for an LLM platform where the set of meaningful signals is bounded and known.
- **JSON expression tree**: A mini-DSL in JSON (e.g., `{op: "gt", left: {metric: "error_rate", window: "5m"}, right: 0.05}`). Still requires an expression evaluator and offers no real advantage over a fixed enum when the signal types are enumerable.

**Rationale:** All LLM-native platforms (Coze, LiteLLM, Helicone) use fixed alert types. PromQL is the pattern for infrastructure monitoring platforms (Grafana) where metrics are open-ended. Hecate's signals are a closed set (8 types), so a fixed enum is simpler, type-safe, and API-friendly. New types are added by extending the enum and registering a new SignalProvider.

### D2: Grafana-standard four-state lifecycle

Alert events follow: `OK → PENDING → FIRING → RESOLVED → OK`, plus `ACKED` as a terminal state for manually acknowledged events.

**Alternatives considered:**
- **Two-state (OK/FIRING)**: Fires immediately on threshold breach. Risks alert fatigue from transient spikes (a 10-second error burst at 3am pages on-call for no reason). Dify and LiteLLM effectively work around this by relying on Prometheus's `for` clause externally, but since we own the evaluator, we should build it in.
- **Three-state without ACK** (OK/PENDING/FIRING): Loses the ability to track human response. Enterprise workflows need ACK to stop escalation.

**Rationale:** The `for` duration (pending phase) is universally adopted by Grafana, Prometheus, Datadog, and Alertmanager. It is the single most important noise-reduction mechanism. ACK is standard in PagerDuty/Opsgenie and prevents escalation from running indefinitely.

### D3: Dedicated asyncio evaluator with PG advisory lock

The alert evaluator runs as a dedicated `asyncio.Task` started in the FastAPI lifespan, not via the existing `ScheduleManager` (APScheduler).

**Alternatives considered:**
- **Reuse ScheduleManager**: Already has advisory-lock support and cron scheduling. But it requires the `[scheduling]` optional dependency group (apscheduler + croniter). Making alerting depend on scheduling creates an unnecessary coupling — alerting should work even if scheduling isn't installed.
- **Temporal workflow**: Hecate already uses Temporal for some paths. A periodic Temporal workflow could evaluate alerts. But Temporal is heavyweight for a simple periodic poll, and Temporal itself is in an optional dependency group.
- **APScheduler standalone**: Would add a new dependency just for alerting when asyncio can do it natively.

**Rationale:** The `MonitoringService._push_loop()` already establishes the pattern of an asyncio background task in the application lifespan. The evaluator follows the same pattern. The PG advisory lock (`pg_try_advisory_lock`) ensures only one node evaluates in a multi-worker deployment — approximately 15 lines of SQL. Default interval is 60 seconds, configurable via `ALERT_EVAL_INTERVAL`.

### D4: TraceModel as the single signal source

All 8 signal types query `TraceModel` (and `CostService` for cost signals) rather than the in-memory `MetricsCollector`.

**Alternatives considered:**
- **MetricsCollector (in-memory)**: Fast, but ephemeral — data is lost on restart. Cannot evaluate rules over historical windows. Not multi-process safe (each worker has its own collector). Unsuitable for alerting where rules span 5-30 minute windows.
- **Dual-source hybrid**: Use MetricsCollector for real-time alerts (sub-minute) and TraceModel for windowed alerts. Adds complexity with no clear v1 benefit — 60-second evaluation cadence is sufficient.

**Rationale:** TraceModel is durable, indexed, multi-process safe, and already powers CostService. Every signal we need is derivable: error rate (`COUNT(status=error)/COUNT(*)`), latency p95 (percentile of `end_time - start_time`), TTFT (`AVG(metadata->ttft_ms)`), token usage (`SUM(usage->total_tokens)`), tool failure rate (`COUNT(status=error AND type=TOOL)/COUNT(type=TOOL)`). Using a single source keeps the SignalProvider registry uniform.

### D5: Weighted moving average for budget forecast

The `cost_monthly_forecast` alert type uses an exponentially-weighted moving average (EWMA) of daily costs, extrapolated to month-end, rather than simple linear projection.

**Alternatives considered:**
- **Simple linear projection** (LiteLLM pattern: `daily_avg = total_spend / days_elapsed; projected = daily_avg * total_days`): Ignores trend changes. If spending accelerates mid-month, the projection lags. If spending drops, it overestimates.
- **Seasonal decomposition** (Datadog anomaly detection): Decomposes into trend + seasonality + residual. Far too complex for v1 and requires weeks of baseline data that a freshly deployed instance won't have.

**Rationale:** EWMA weights recent days more heavily (exponential decay), so it captures trend changes (acceleration/deceleration) without requiring seasonal baseline data. The formula: `recent_avg = sum(cost[i] * 0.5^(7-i)) / sum(0.5^(7-i))` for the last 7 days, then `projected_monthly = recent_avg * days_in_month`. This is the standard approach in budget-management tools (AWS Budgets, CloudHealth) and is simple to implement and reason about.

### D6: Per-platform built-in message templates

Each IM channel type (Feishu, WeCom, DingTalk, Slack) has a built-in message template that formats alert events into the platform's native message format, rather than requiring users to craft raw webhook payloads.

**Alternatives considered:**
- **Generic JSON only**: User configures a Jinja2 template or raw payload. Maximum flexibility but terrible UX — every user reinvents the same Feishu card JSON. Not viable for an enterprise product.
- **Single unified template**: One message format sent to all channels. Loses platform-specific features (Feishu interactive cards, Slack Block Kit buttons for ACK).

**Rationale:** Each Chinese IM platform has a distinct payload format (Feishu uses `card` JSON with interactive elements; WeCom uses `markdown` with limited formatting; DingTalk uses `markdown` with at-sign mentions; Slack uses `blocks` array with Block Kit). Pre-built templates mean users just paste a webhook URL and get a properly formatted alert with action buttons. The templates are defined as Python functions that take an `AlertEventModel` and return the platform-specific JSON payload.

### D7: Escalation policies as reusable entities

`EscalationPolicyModel` is a separate table that rules reference via `escalation_policy_id`, rather than embedding escalation steps directly in `AlertRuleModel`.

**Alternatives considered:**
- **Embed in rule**: Simpler schema (one fewer table), but escalation logic is typically shared across many rules. A production deployment might have 50 rules but only 2-3 escalation policies (e.g., "standard" and "critical-only"). Duplicating steps across 50 rules is error-prone.
- **Global config**: A single YAML/config-based escalation policy. Not multi-tenant friendly — different workspaces may have different on-call rotations.

**Rationale:** Escalation policies are inherently reusable ( PagerDuty, Opsgenie all model them as first-class entities). A default policy is seeded via migration (step 0: webhook + WebSocket, step 1 after 15min: email, repeat every 60min). Users can create custom policies per workspace.

### D8: Silence windows as time-bounded matchers

`AlertSilenceModel` suppresses notifications for matched rules during a `[start_at, end_at]` window, with matchers on `rule_ids` and/or `severity`.

**Alternatives considered:**
- **Disable rule**: Temporarily setting `rule.enabled = false` loses the evaluation history and doesn't auto-resume. Also requires manual re-enablement.
- **Per-channel mute**: Mute at the notification channel level. Too coarse — a channel might serve multiple rules where only some should be silenced.

**Rationale:** This matches Grafana Alertmanager's silencing model. Silence windows are first-class entities with audit trail (who silenced, why, when). The evaluator checks active silences before dispatching notifications. Silences auto-expire at `end_at`, so maintenance windows are hands-off.

## Risks / Trade-offs

- **[Evaluation latency]** 60-second polling means alerts fire up to 60 seconds after a threshold breach. → Acceptable for v1. Streaming evaluation (webhook on trace completion) can be added later for sub-minute alerts. The `for` duration already adds minutes of delay, so the poll interval is not the bottleneck.

- **[Database load]** Each evaluation cycle queries TraceModel for every enabled rule. With many rules and large trace volumes, this could be expensive. → Mitigation: (1) rules are window-bounded (5-30 min), so queries hit recent data with time indexes; (2) SignalProvider implementations use `COUNT`/`SUM` aggregations, not row-level scans; (3) a max rule count guard can be added if needed.

- **[Single evaluator node]** Advisory lock means only one node evaluates. If that node is down, alerts are missed. → Mitigation: the lock auto-releases on connection close (session-level lock), so a healthy node picks up within one evaluation cycle. For HA, a future enhancement can use a leader-election mechanism.

- **[TTFT accuracy]** TTFT is measured in LLMWorker by timestamping the first chunk arrival. If the LLM provider buffers differently, TTFT may not reflect true first-token time. → Mitigation: document the measurement semantics clearly. The metric is still useful for relative comparison (before/after optimization).

- **[Email dependency]** Adding `aiosmtplib` introduces an optional dependency. → Mitigation: place in `[observability]` group. Email channel gracefully degrades if SMTP is not configured (logs a warning, other channels still fire).

- **[Webhook delivery reliability]** HTTP POST to external webhooks can fail (timeout, 5xx). → Mitigation: 3 retries with exponential backoff. Failed deliveries are logged but do not block the evaluation cycle. A future enhancement can add a dead-letter table.
