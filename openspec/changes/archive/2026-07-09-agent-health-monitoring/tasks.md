## 1. Configuration

- [x] 1.1 Add health threshold settings to `core/config.py`: `AGENT_HEALTH_ERROR_RATE_WARNING: float = 0.05`, `AGENT_HEALTH_ERROR_RATE_CRITICAL: float = 0.15`, `AGENT_HEALTH_LATENCY_WARNING_MS: int = 10000`, `AGENT_HEALTH_LATENCY_CRITICAL_MS: int = 30000`
- [x] 1.2 Add `AGENT_HEALTH_SCORE_WEIGHTS: dict = {"error_rate": 0.5, "latency": 0.3, "activity": 0.2}` setting (JSON dict for weighted score formula)

## 2. AgentHealthService — Core Logic

- [x] 2.1 Create `src/hecate/services/ops_center/agent_health.py` with `AgentHealthService` class (constructor takes `AsyncSession`, same pattern as `ToolAnalyticsService`)
- [x] 2.2 Implement `_classify_health_status(error_rate, p95_latency_ms, settings)` → returns `"healthy"` / `"warning"` / `"critical"` / `"unknown"` using configurable thresholds (worst-of-dimension logic)
- [x] 2.3 Implement `_compute_health_score(error_rate, p95_latency_ms, session_count, settings)` → returns 0–100 integer or `None` for unknown agents. Uses weighted formula: error_rate dimension = `max(0, 100 - error_rate * 500)`, latency dimension = `max(0, 100 - (p95_latency_ms / critical_threshold_ms) * 100)`, activity dimension = `min(100, session_count / 10 * 100)`. Weights from `AGENT_HEALTH_SCORE_WEIGHTS`.
- [x] 2.4 Implement `_compute_p95(values)` helper (reuse logic from `tool_analytics.py` — Python-side percentile for cross-dialect compatibility)

## 3. AgentHealthService — Query Methods

- [x] 3.1 Implement `get_fleet_overview(start_date, end_date)` → aggregate query grouping root traces (`type="trace"`) by `agent_id`. Returns `{total_agents, healthy_count, warning_count, critical_count, unknown_count, fleet_error_rate, fleet_p95_latency_ms, top_degraded: [...]}`
- [x] 3.2 Implement `get_agent_health(agent_id, start_date, end_date)` → per-agent metrics from root traces. Returns `{agent_id, total_sessions, error_count, error_rate, success_rate, avg_latency_ms, p95_latency_ms, last_active_at, health_status, health_score, score_breakdown}`
- [x] 3.3 Implement `get_agent_trends(agent_id, days=7, granularity="daily")` → time-series of session count, errors, error_rate, avg_latency, p95_latency per time bucket (Python-side bucketing, same as `ToolAnalyticsService.get_trends`)

## 4. AgentHealthService — Tests

- [x] 4.1 Test `_classify_health_status()`: healthy (low error + low latency), warning (high error only), critical (high latency), unknown (no data)
- [x] 4.2 Test `_classify_health_status()` with custom thresholds from settings
- [x] 4.3 Test `_compute_health_score()`: perfect score (0% error, low latency, 20 sessions), degraded score (10% error), null score for unknown
- [x] 4.4 Test `_compute_health_score()` with custom weights from settings
- [x] 4.5 Test `get_fleet_overview()` with mixed health statuses (multiple agents, varying error rates and latencies)
- [x] 4.6 Test `get_fleet_overview()` with no data (returns zeros, empty top_degraded)
- [x] 4.7 Test `get_fleet_overview()` top_degraded limited to 10 entries sorted by score ascending
- [x] 4.8 Test `get_agent_health()` with active agent (verify all fields including score_breakdown)
- [x] 4.9 Test `get_agent_health()` with inactive agent (status=unknown, score=null)
- [x] 4.10 Test `get_agent_trends()` returns correct daily buckets for 7-day range
- [x] 4.11 Test `get_agent_trends()` with empty data returns empty list

## 5. Agent Health API

- [x] 5.1 Create `src/hecate/api/management/agent_health.py` router with prefix `/api/ops-center/agents`
- [x] 5.2 Implement `GET /overview` endpoint (start_date, end_date query params) → fleet overview dict
- [x] 5.3 Implement `GET /{agent_id}/health` endpoint (start_date, end_date query params) → per-agent health dict, 404 if agent has no data
- [x] 5.4 Implement `GET /{agent_id}/trends` endpoint (days, granularity query params) → time-series list
- [x] 5.5 Register `agent_health_router` in `main.py`

## 6. Frontend — Agent Health Dashboard

- [x] 6.1 Create `web/src/app/(dashboard)/ops-center/agents/page.tsx` with fleet status summary cards (healthy/warning/critical/unknown counts with color-coded badges: green/yellow/red/gray)
- [x] 6.2 Add agent fleet table (columns: agent name, health status badge, health score, error rate, P95 latency, last active — sortable by score)
- [x] 6.3 Add time range selector (24h / 7d / 30d) that re-fetches overview data
- [x] 6.4 Add drill-down: clicking an agent row navigates to detail view showing health trends chart (recharts line chart for error_rate and latency over time) and score breakdown
- [x] 6.5 Add empty state handling (no agents / no data message)
- [x] 6.6 Add "Agents" sub-navigation item under "Ops Center" in `web/src/components/sidebar.tsx` linking to `/ops-center/agents`

## 7. Verification

- [x] 7.1 Run `ruff check src/hecate/ tests/ && ruff format --check src/ tests/` — 0 errors
- [x] 7.2 Run `mypy src/` — 0 errors
- [x] 7.3 Run `python -m pytest tests/test_ops_center/test_agent_health.py -q` — all pass
- [ ] 7.4 Verify end-to-end: trigger a session execution, confirm fleet overview shows the agent with correct health status and score
