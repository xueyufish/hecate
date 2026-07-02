## Why

Hecate has full-chain tracing (8.1), real-time monitoring (8.2), and a cost dashboard (8.3), but no way to proactively notify operators when things go wrong. Today, problems are only discovered when a human manually checks dashboards. An alerting system closes this gap—automatically evaluating threshold and budget-forecast rules against trace data, and dispatching notifications through enterprise IM channels (Feishu/WeCom/DingTalk/Slack), email, and in-app WebSocket. This completes the observability stack (8.x) and is a hard requirement for any enterprise-grade platform.

## What Changes

- **Add AlertRuleModel** — per-workspace rules with 8 alert types (error_rate, latency_p95, latency_ttft, token_usage, cost_daily, cost_monthly_forecast, tool_failure_rate, success_rate), threshold, evaluation window, `for` duration, severity, and scope filters (agent_id, model).
- **Add AlertEventModel** — alert instances with Grafana-standard state machine: PENDING → FIRING → RESOLVED, plus manual ACK.
- **Add AlertSilenceModel** — maintenance windows that suppress notifications for specified rules or severities during a time range.
- **Add EscalationPolicyModel** — multi-step escalation (e.g., step 0: webhook, step 1 after 15min: SMS, step 2 after 30min: phone) with repeat interval.
- **Add NotificationChannelModel** — configurable notification targets with 7 channel types (webhook_feishu, webhook_wecom, webhook_dingtalk, webhook_slack, webhook_generic, websocket, email) and built-in message templates per IM platform.
- **Add AlertEvaluator** — asyncio background task with PostgreSQL advisory lock, runs every 60s, queries TraceModel/CostService, evaluates rules, manages state transitions, and dispatches notifications.
- **Add SignalProvider registry** — pluggable signal sources, one per AlertType, each queries TraceModel or CostService for the relevant metric.
- **Add NotificationDispatcher** — routes firing events through escalation policies to the right channels at the right time, with built-in message templates for Feishu (card), WeCom (markdown), DingTalk (markdown), Slack (Block Kit), and email (HTML).
- **Instrument TTFT in LLMWorker** — write `ttft_ms` and `total_latency_ms` into TraceModel metadata during streaming LLM calls to support the latency_ttft alert type.
- **Add 5 API routers** — `/api/alerts/rules`, `/api/alerts/events`, `/api/alerts/silences`, `/api/alerts/channels`, `/api/alerts/escalation-policies` with full CRUD.
- **Add alert config settings** — ALERT_EVAL_INTERVAL, SMTP connection settings, default escalation policy.

## Capabilities

### New Capabilities

- `alerting`: Alert rule management, evaluation engine, notification dispatch, escalation policies, silence windows, and alert event lifecycle.

### Modified Capabilities

- `full-chain-tracing`: LLMWorker instruments `ttft_ms` into TraceModel metadata during streaming responses.

## Impact

- **New files**: `models/alert.py` (5 models + 4 enums + schemas), `services/alert_service.py`, `services/alert_evaluator.py`, `services/notification_dispatcher.py`, `services/signal_provider.py`, `api/management/alerts.py` (5 routers), `alembic/versions/xxxx_add_alerting.py`.
- **Modified files**: `engine/workers/llm_worker.py` (TTFT instrumentation), `main.py` (router registration + evaluator startup), `core/config.py` (alert settings), `tests/conftest.py` (module import).
- **New dependencies**: `aiosmtplib` (email channel, `[observability]` group).
- **Database**: 5 new tables (alert_rules, alert_events, alert_silences, escalation_policies, notification_channels) with indexes and a seed default escalation policy.
- **API**: 5 new router groups under `/api/alerts/`.
- **Background**: New asyncio task started in application lifespan for alert evaluation.
