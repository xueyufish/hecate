## Why

Hecate's TraceModel is now populated with real execution data (Change 1: `otel-trace-bridge-tool-analytics` bridged OTel spans to TraceModel), but operators have no visibility into individual agent health. When an agent's error rate spikes or latency degrades, there is no dashboard to surface the problem — operators must manually query traces. Competing platforms (Salesforce Agentforce, Microsoft Agent 365) provide fleet-level health overviews with drill-down to individual agents. Hecate needs the same.

This change (feature 8.9a) is the second of four Ops Center changes. It derives per-agent health metrics from the TraceModel data that Change 1 now populates, following the same SQL-aggregation pattern as `ToolAnalyticsService` and `ModelMonitoringService`. No new infrastructure is required — TraceModel already stores `agent_id`, `status`, `start_time`, `end_time`, and `type` for every execution span.

## What Changes

- **New: `AgentHealthService`** — Aggregation service that queries TraceModel for per-agent health metrics: total executions, error rate, average/P95 latency, success rate, last active timestamp, and uptime ratio. Follows the same SQL-query pattern as `ToolAnalyticsService`.
- **New: Health status taxonomy** — Three-level classification (`healthy` / `warning` / `critical`) computed from configurable thresholds on error rate and latency. Each agent receives a computed health status on every query.
- **New: Configurable health score formula** — Weighted scoring (0–100) combining error rate, latency, and activity. Weights are configurable via settings. SLA breach detection flags agents that cross threshold boundaries.
- **New: Fleet overview** — Aggregate view across all agents showing distribution of health statuses (N healthy, N warning, N critical), top degraded agents, and fleet-level error/latency trends.
- **New: Per-agent drill-down** — Individual agent detail view with time-series trends, recent execution traces, and health score breakdown.
- **New: REST API** — `GET /api/ops-center/agents/*` endpoints for fleet overview, per-agent health, trends, and alerts.
- **New: Frontend dashboard** — Agent health dashboard at `web/src/app/(dashboard)/ops-center/agents/` with fleet status cards, health distribution chart, agent table with status indicators, and drill-down detail view.
- **New: Sidebar sub-entry** — "Agents" navigation item under the existing "Ops Center" section.

## Capabilities

### New Capabilities

- `agent-health-monitoring`: Per-agent health monitoring derived from TraceModel. Includes health status taxonomy (healthy/warning/critical), configurable health score formula with SLA breach detection, fleet overview aggregation, per-agent drill-down with trends, REST API, and frontend dashboard.

### Modified Capabilities

_(none — this change introduces a new capability without modifying existing spec requirements. It reads from TraceModel (populated by `otel-trace-bridge`) but does not change tracing behavior.)_

## Impact

- **Services layer**: New `AgentHealthService` in `services/ops_center/`. Follows `ToolAnalyticsService` pattern — pure SQL aggregation queries on TraceModel.
- **API layer**: New router at `api/management/agent_health.py`. Registered in `main.py`.
- **Config**: New settings for health thresholds (`AGENT_HEALTH_ERROR_RATE_WARNING`, `AGENT_HEALTH_ERROR_RATE_CRITICAL`, `AGENT_HEALTH_LATENCY_P95_WARNING_MS`, `AGENT_HEALTH_LATENCY_P95_CRITICAL_MS`, `AGENT_HEALTH_SCORE_WEIGHTS`).
- **Frontend**: New `ops-center/agents/` page + sidebar sub-entry under "Ops Center".
- **Dependencies**: No new packages — reuses existing SQLAlchemy async, TraceModel, and frontend chart libraries.
- **Tests**: New test files for AgentHealthService and API endpoints.
