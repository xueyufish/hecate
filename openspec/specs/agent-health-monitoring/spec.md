## ADDED Requirements

### Requirement: Agent fleet overview endpoint
The system SHALL expose `GET /api/ops-center/agents/overview` that returns aggregate fleet health for a time range: total agents, distribution by health status (healthy/warning/critical/unknown counts), fleet-level error rate, fleet-level average P95 latency, and list of top degraded agents (sorted by health score ascending). Supports `start_date`, `end_date` query parameters.

#### Scenario: Fleet overview with mixed health statuses
- **WHEN** a client requests `GET /api/ops-center/agents/overview?start_date=2026-07-01&end_date=2026-07-08`
- **THEN** the system returns `{total_agents, healthy_count, warning_count, critical_count, unknown_count, fleet_error_rate, fleet_p95_latency_ms, top_degraded: [{agent_id, agent_name, health_status, health_score, error_rate, p95_latency_ms}]}`

#### Scenario: Fleet overview with no active agents
- **WHEN** a client requests fleet overview for a time range where no root trace spans exist
- **THEN** the system returns `{total_agents: 0, healthy_count: 0, warning_count: 0, critical_count: 0, unknown_count: 0, fleet_error_rate: 0.0, fleet_p95_latency_ms: 0.0, top_degraded: []}`

#### Scenario: Top degraded agents limited to 10
- **WHEN** more than 10 agents have degraded health (warning or critical)
- **THEN** `top_degraded` contains at most 10 entries sorted by health_score ascending

### Requirement: Per-agent health metrics endpoint
The system SHALL expose `GET /api/ops-center/agents/{agent_id}/health` that returns health metrics for a specific agent: total sessions, error count, error rate, success rate, average session latency, P95 session latency, last active timestamp, computed health status, and computed health score with dimension breakdown.

#### Scenario: Health metrics for active agent
- **WHEN** a client requests `GET /api/ops-center/agents/{agent_id}/health?start_date=2026-07-01&end_date=2026-07-08`
- **THEN** the system returns `{agent_id, total_sessions, error_count, error_rate, success_rate, avg_latency_ms, p95_latency_ms, last_active_at, health_status, health_score, score_breakdown: {error_rate_dimension, latency_dimension, activity_dimension}}`

#### Scenario: Health metrics for agent with no activity
- **WHEN** a client requests health for an agent with zero root trace spans in the time range
- **THEN** the system returns `{agent_id, total_sessions: 0, health_status: "unknown", health_score: null, ...}` with null score and unknown status

### Requirement: Health status taxonomy
The system SHALL classify each agent into one of four health statuses: `healthy`, `warning`, `critical`, or `unknown`. Classification uses two dimensions — error rate and P95 session latency — each with configurable warning and critical thresholds. The overall status is the worst dimension status (if either dimension is critical, the agent is critical). Agents with zero sessions in the time range are classified as `unknown`.

#### Scenario: Healthy agent
- **WHEN** an agent has error_rate ≤ warning threshold (default 5%) AND p95_latency ≤ warning threshold (default 10000ms)
- **THEN** the agent's health_status is `healthy`

#### Scenario: Warning agent — high error rate only
- **WHEN** an agent has error_rate > 5% but ≤ 15% AND p95_latency ≤ 10000ms
- **THEN** the agent's health_status is `warning`

#### Scenario: Critical agent — high latency
- **WHEN** an agent has p95_latency > 30000ms (critical threshold)
- **THEN** the agent's health_status is `critical` regardless of error rate

#### Scenario: Unknown agent — no activity
- **WHEN** an agent has zero root trace spans in the queried time range
- **THEN** the agent's health_status is `unknown`

#### Scenario: Custom thresholds via configuration
- **WHEN** `AGENT_HEALTH_ERROR_RATE_WARNING` is set to 0.03 and an agent has error_rate 4%
- **THEN** the agent's health_status is `warning` (4% > 3% custom threshold)

### Requirement: Configurable health score formula
The system SHALL compute a health score (0–100) for each agent using a weighted formula across three dimensions: error rate (default weight 50%), latency (default weight 30%), and activity (default weight 20%). Weights are configurable via the `AGENT_HEALTH_SCORE_WEIGHTS` setting (JSON object). Dimension scores: error rate dimension = `max(0, 100 - error_rate * 500)`, latency dimension = `max(0, 100 - (p95_latency_ms / critical_threshold_ms) * 100)`, activity dimension = `min(100, session_count / 10 * 100)` (normalized to 10-session baseline). Agents with unknown status receive `null` score.

#### Scenario: Perfect health score
- **WHEN** an agent has 0% error rate, p95 latency of 1000ms (well under warning), and 20 sessions
- **THEN** the health_score is 100 (all dimension scores are 100)

#### Scenario: Degraded score from high error rate
- **WHEN** an agent has 10% error rate, good latency, and normal activity
- **THEN** the error_rate_dimension = max(0, 100 - 0.10 * 500) = 50, and health_score = 50 * 0.5 + ~100 * 0.3 + ~100 * 0.2 = ~95 (weighted)

#### Scenario: Custom weights via configuration
- **WHEN** `AGENT_HEALTH_SCORE_WEIGHTS` is set to `{"error_rate": 0.8, "latency": 0.1, "activity": 0.1}`
- **THEN** the health_score uses 80% error rate weight, 10% latency weight, 10% activity weight

#### Scenario: Unknown status yields null score
- **WHEN** an agent has zero sessions (unknown status)
- **THEN** health_score is `null`

### Requirement: Per-agent health trends endpoint
The system SHALL expose `GET /api/ops-center/agents/{agent_id}/trends` that returns daily time-series of health metrics: session count, error count, error rate, average latency, and P95 latency per day. Supports `days` parameter (1–90, default 7) and `granularity` parameter ("daily", "hourly", "weekly").

#### Scenario: Daily trends for past 7 days
- **WHEN** a client requests `GET /api/ops-center/agents/{agent_id}/trends?days=7&granularity=daily`
- **THEN** the system returns a list of `{date, total_sessions, errors, error_rate, avg_latency_ms, p95_latency_ms}` entries, one per day

#### Scenario: Empty trends for inactive agent
- **WHEN** an agent has no trace data in the requested period
- **THEN** the system returns an empty list `[]`

### Requirement: Agent health data sourced from root trace spans
The system SHALL derive all agent health metrics from TraceModel records where `type="trace"` (root session spans). Each root span represents one full agent session execution with `agent_id`, `status`, `start_time`, and `end_time`. The system SHALL filter by `~TraceModel.deleted` in all queries.

#### Scenario: Count sessions from root traces
- **WHEN** computing total_sessions for an agent
- **THEN** the system counts TraceModel rows where `type="trace"` AND `agent_id={agent_id}` AND `start_time` within range AND `deleted=false`

#### Scenario: Compute error rate from root trace status
- **WHEN** computing error_rate for an agent
- **THEN** the system counts rows where `status="error"` divided by total rows for that agent in the time range

### Requirement: Configurable health thresholds via settings
The system SHALL read health classification thresholds from application settings: `AGENT_HEALTH_ERROR_RATE_WARNING` (default 0.05), `AGENT_HEALTH_ERROR_RATE_CRITICAL` (default 0.15), `AGENT_HEALTH_LATENCY_WARNING_MS` (default 10000), `AGENT_HEALTH_LATENCY_CRITICAL_MS` (default 30000), and `AGENT_HEALTH_SCORE_WEIGHTS` (default `{"error_rate": 0.5, "latency": 0.3, "activity": 0.2}`).

#### Scenario: Default thresholds applied
- **WHEN** no custom health settings are configured
- **THEN** the system uses warning error rate 5%, critical error rate 15%, warning latency 10s, critical latency 30s

#### Scenario: Custom thresholds override defaults
- **WHEN** `AGENT_HEALTH_ERROR_RATE_CRITICAL` is set to 0.10
- **THEN** agents with error_rate > 10% are classified as critical on the error rate dimension

### Requirement: Frontend agent health dashboard
The system SHALL provide a React dashboard page at `/ops-center/agents` displaying: fleet status summary cards (healthy/warning/critical/unknown counts with color-coded badges), health distribution chart, agent fleet table with sortable columns (name, status, score, error rate, P95 latency, last active), and a drill-down link to per-agent detail view.

#### Scenario: Fleet overview displayed on page load
- **WHEN** the user navigates to `/ops-center/agents`
- **THEN** the page fetches `GET /api/ops-center/agents/overview` and displays status summary cards and the agent fleet table

#### Scenario: Click agent row to view details
- **WHEN** the user clicks an agent row in the fleet table
- **THEN** the page navigates to the agent detail view showing health trends chart and score breakdown

#### Scenario: Time range filter
- **WHEN** the user selects a different time range (e.g., last 24h, last 7d, last 30d)
- **THEN** the page re-fetches overview data with updated `start_date` and `end_date` parameters

### Requirement: Sidebar navigation entry
The system SHALL add an "Agents" sub-navigation item under the existing "Ops Center" section in the sidebar, linking to `/ops-center/agents`.

#### Scenario: Sidebar displays Agents link
- **WHEN** the sidebar is rendered
- **THEN** under "Ops Center" section, "Agents" and "Tools" items are both visible as sibling links
