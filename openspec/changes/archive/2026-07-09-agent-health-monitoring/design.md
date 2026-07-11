## Context

Change 1 (`otel-trace-bridge-tool-analytics`) bridged OTel spans to TraceModel and built `ToolAnalyticsService`. TraceModel now receives real execution data: root session spans (`type="trace"`, `name="session:{id}"`), LLM spans (`type="generation"`), and tool spans (`type="tool"`). Every span carries `agent_id`, `status` (started/completed/error), `start_time`, `end_time`, and `session_id`.

The existing `ToolAnalyticsService` (`services/ops_center/tool_analytics.py`) demonstrates the SQL-aggregation pattern: constructor takes `AsyncSession`, methods run `func.count()` / `func.max()` queries with `~TraceModel.deleted` filter, P95 computed in Python for cross-dialect compatibility, all methods return `dict[str, Any]`.

`ModelMonitoringService` (`services/monitoring/`) follows a similar pattern for per-model metrics from TraceModel.

**Key data source**: Root trace spans (`type="trace"`) represent full agent session executions. Each has `agent_id`, `status`, `start_time`, `end_time`. These are the primary signal for agent health.

## Goals / Non-Goals

**Goals:**

- Per-agent health metrics derived from TraceModel: total sessions, error rate, avg/P95 session latency, success rate, last active timestamp
- Three-level health status taxonomy: `healthy` / `warning` / `critical` with configurable thresholds
- Configurable health score (0–100) combining error rate and latency
- SLA breach detection: flag agents exceeding threshold boundaries
- Fleet overview: aggregate distribution (N healthy/warning/critical), top degraded agents
- Per-agent drill-down: time-series trends, recent traces, score breakdown
- REST API + frontend dashboard following `ToolAnalyticsService` pattern

**Non-Goals:**

- User satisfaction score / escalation rate — depend on conversation feedback infrastructure (Change 3: `conversation-analytics`). Health score formula has a placeholder weight reserved for future satisfaction data.
- Real-time WebSocket push — REST polling only (same as tool analytics)
- Alerting integration — existing AlertService handles alert routing; health metrics are read-only queries
- Unified Ops Center Dashboard (8.9) — Change 4 aggregates 8.9a/b/c data sources
- Conversation quality scoring (8.9b v2) — separate change

## Decisions

### Decision 1: SQL-derived health metrics from root trace spans

**Choice**: Query TraceModel where `type="trace"` (root session spans) for per-agent session-level metrics. Each root span represents one full agent execution with `agent_id`, `status`, `start_time`, `end_time`.

**Rationale**: Root trace spans are created by `PregelRuntime.execute()` (Change 1). They carry `agent_id` from the execution context. Counting root spans = session count; error root spans = failed sessions. This is the same data source as `ToolAnalyticsService` and `ModelMonitoringService` — no new infrastructure needed.

**Alternatives considered**:
- **Query all span types per agent**: Too noisy. Tool and LLM spans are child-level signals; session-level (root trace) is the right granularity for fleet health.
- **Use MetricsStore (real-time-monitoring)**: Different paradigm (time-windowed counters vs. SQL aggregation). Would require wiring MetricsStore recording into PregelRuntime. Over-engineering for a dashboard.

### Decision 2: Three-level health status taxonomy with configurable thresholds

**Choice**: Compute `healthy` / `warning` / `critical` status per agent based on two dimensions:
- Error rate: warning at >5%, critical at >15% (configurable)
- P95 session latency: warning at >10s, critical at >30s (configurable)

Status = worst of the two dimensions (if either dimension is critical, agent is critical).

**Rationale**: Two-dimensional status avoids false positives (e.g., high latency but zero errors = warning, not critical). Worst-of-dimension is the industry standard (Salesforce Agentforce uses composite health score with per-dimension thresholds).

**Alternatives considered**:
- **Single health score threshold**: Less actionable. Operators can't tell if degradation is latency-driven or error-driven.
- **Machine-learning anomaly detection**: Over-engineering for v1. The existing `ModelMonitoringService` uses z-score drift detection — that's model-level. Agent-level starts with threshold-based, can add z-score later.

### Decision 3: Weighted health score formula (0–100)

**Choice**: Health score = weighted sum of dimension scores:
- Error rate dimension (weight: 50%): `max(0, 100 - error_rate * 500)` — 0% errors = 100, 20% errors = 0
- Latency dimension (weight: 30%): `max(0, 100 - (p95_latency_ms / critical_threshold_ms) * 100)` — under warning = ~100, at critical = 0
- Activity dimension (weight: 20%): `min(100, session_count / expected_sessions * 100)` — normalized to recent baseline

All weights configurable via `AGENT_HEALTH_SCORE_WEIGHTS` setting (JSON dict).

**Rationale**: Weighted scoring is transparent and tunable. Operators can adjust weights without code changes. The 50/30/20 default prioritizes error rate (most impactful), then latency, then activity. This matches Salesforce Agentforce's "composite health score" approach.

**Alternatives considered**:
- **Pure threshold status (no score)**: Less granular. Score enables sorting agents by degradation severity in the fleet view.
- **ML-based scoring**: Black box. Configurable formula is auditable and debuggable.

### Decision 4: Fleet overview as aggregate query

**Choice**: `get_fleet_overview()` runs a single SQL query grouping root traces by `agent_id`, computing per-agent aggregates in SQL, then classifying status and counting distribution in Python.

**Rationale**: Single round-trip for the fleet view. Follows `ToolAnalyticsService.get_overview()` pattern (SQL aggregate + Python post-processing for P95 and derived metrics).

### Decision 5: No persistence — compute on demand

**Choice**: Health status and score are computed on every API request. No health snapshot table, no background refresh job.

**Rationale**: TraceModel queries with indexed `agent_id` + `type` + `start_time` columns are fast (<100ms for 100K rows). Dashboard polling interval is 30–60s. Adding a snapshot table + refresh job is premature optimization. If performance degrades at scale, a materialized view or cache layer can be added later without API changes.

**Alternatives considered**:
- **Background health snapshot job**: Adds complexity (scheduler, snapshot table, stale data). Not needed at current scale.
- **Redis cache**: Same trade-off. Keep it simple for v1.

## Risks / Trade-offs

- **[Risk] Missing satisfaction/escalation metrics** → Health score uses only error rate + latency + activity. Satisfaction score weight (currently 0%) is reserved for future integration with Change 3 (conversation-analytics). Documented in Non-Goals.
- **[Risk] Agents with zero recent activity** → Agents with no root traces in the time window get `status=unknown`, `score=None`, excluded from fleet distribution counts. Prevents skewing the healthy count.
- **[Trade-off] Python-side P95 computation** → Same as ToolAnalyticsService. Cross-dialect compatible (SQLite/PostgreSQL/MySQL). At 100K+ traces per agent, consider SQL percentile functions. Not a concern for v1.
- **[Trade-off] No historical health tracking** → No time-series of health score over time. Fleet trends endpoint shows underlying metrics (error rate, latency) but not the computed score. Adding a health history table is a future enhancement.
