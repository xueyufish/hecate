## Context

Ops Center has three sub-dashboards built in Changes 1-3:

- **ToolAnalyticsService** (`services/ops_center/tool_analytics.py`) — `get_overview()`, `get_tool_details()`, `get_trends()`, `get_top_errors()`. API at `/api/ops-center/tools/*`.
- **AgentHealthService** (`services/ops_center/agent_health.py`) — `get_fleet_overview()`, `get_agent_health()`, `get_agent_trends()`. API at `/api/ops-center/agents/*`.
- **ConversationAnalyticsService** (`services/ops_center/conversation_analytics.py`) — `get_overview()`, `get_quality_distribution()`, `get_topics()`, `get_low_quality()`, `get_conversation_turns()`, `get_trends()`. API at `/api/ops-center/conversations/*`.

All three services follow the same pattern: constructor takes `AsyncSession`, methods return `dict[str, Any]`, queries use SQLAlchemy `func.count()` / `func.avg()` with `~Model.deleted` filter.

The sidebar currently has 3 sibling links under "Ops Center": Tools (`/ops-center/tools`), Agent Health (`/ops-center/agents`), Conversations (`/ops-center/conversations`). The "Ops Center" link itself points to `/ops-center/tools`.

**Industry research (Salesforce, Palantir, BFF pattern):**
- Salesforce Agentforce uses a "Semantic Data Model (SDM)" as unified backend source of truth; the Command Center UI consumes aggregated data, not raw service calls.
- Palantir AIP uses a three-layer architecture: Data/Ontology → Logic → Application/Dashboard. Dashboards never call multiple services directly.
- BFF (Backend for Frontend) pattern: "Aggregate: fan out to downstream services in parallel. Reshape: transform data for the specific UI. Handle partial failures gracefully."

## Goals / Non-Goals

**Goals:**

- Single backend aggregation endpoint (`GET /api/ops-center/overview`) that fans out to all three Ops Center services in parallel
- Partial failure handling: if one service fails, return `null` for that section with error metadata (not a 500)
- Unified dashboard page with summary cards from all three subsystems
- Cross-source Recent Activity Feed (critical agents, tool failures, low-quality conversations)
- Quick links to sub-dashboards
- Sidebar "Ops Center" link points to the new overview page

**Non-Goals:**

- Custom Dashboard Builder (O8) — planned enhancement for P4+, independent of this change
- Role-based dashboard personalization — future enhancement
- Real-time WebSocket updates — REST polling only (same as sub-dashboards)
- New data sources (alerts, audit, cost) — only aggregate existing 8.9a/b/c data
- Sidebar hierarchy restructuring — keep flat sibling links, just change the "Ops Center" target

## Decisions

### Decision 1: Backend aggregation (BFF pattern, not frontend parallel fetch)

**Choice**: Single `GET /api/ops-center/overview` endpoint that calls all three services in parallel via `asyncio.gather(return_exceptions=True)`.

**Rationale**: Industry standard (Salesforce SDM, Palantir Ontology queries, BFF pattern). Key advantages:
- Partial failure handling: backend returns `null` for failed sources, frontend renders degraded state
- Response shaping: backend prunes each service's response to only what the overview cards need
- Caching: 30-60 second TTL at backend level (future enhancement, not blocking)
- Latency: one HTTP request vs three (PayPal measured ≥700ms per round trip at p99)

**Alternatives considered**:
- **Frontend `Promise.all`**: Zero backend work, but no partial failure handling, 3x HTTP latency, no caching. BFF research explicitly warns against this for dashboards.
- **GraphQL**: Over-engineering for 3 data sources. Adds operational complexity.

### Decision 2: Only aggregate existing 8.9a/b/c data

**Choice**: Overview page shows metrics from Agent Health, Tool Analytics, and Conversation Analytics only. No alerts, audit logs, cost trends, or deployment status.

**Rationale**: The roadmap explicitly defines 8.9 as "aggregation layer on top of 8.9a/b/c data sources." Adding new data sources would expand scope from M to L. Those systems have their own dashboards.

**Alternatives considered**:
- **Include alerts/audit**: Would require querying AlertService and AuditMiddleware. Scope creep. Those have their own UI.

### Decision 3: Flat sidebar with updated link target

**Choice**: Keep 4 sibling links under "Ops Center" section. Change "Ops Center" link from `/ops-center/tools` to `/ops-center` (new overview page). No collapsible tree structure.

**Rationale**: Salesforce Agentforce Studio uses flat tabs (Analytics, Optimization, Health, Testing Center), not a collapsible tree. Current sidebar is a flat list — implementing a collapsible section is unnecessary complexity for 4 links.

**Alternatives considered**:
- **Collapsible sidebar section**: More navigation hierarchy but adds UI complexity (state management, expand/collapse animation) for minimal benefit.

### Decision 4: Recent Activity Feed from existing queries

**Choice**: Build a cross-source activity feed by querying each subsystem for recent anomalies:
- Agent Health: agents with `health_status = "critical"` or `"warning"`
- Tool Analytics: tools with recent error spikes (from `get_top_errors`)
- Conversation Analytics: conversations with `quality_score < 0.5`

Merge and sort by timestamp. No new data model — reuses existing queries.

**Rationale**: Operators need a single feed of "what needs attention" rather than checking 3 dashboards. This is the highest-value feature of the unified dashboard. Salesforce has a similar concept ("flag low-performing topics and widespread configuration gaps").

## Risks / Trade-offs

- **[Risk] Aggregation latency** → Three parallel service calls. If one is slow, total latency = slowest service. Mitigation: each service query is indexed and fast (<100ms). Total should be <300ms. Future: add per-call timeout and return partial results.
- **[Risk] Partial failure confusion** → If ToolAnalytics fails, the overview shows `null` for that card. Mitigation: display "Data unavailable" with a retry button, not an error page.
- **[Trade-off] No caching in v1** → Every overview request triggers 3 service calls. Acceptable for low-traffic admin dashboard. Future: add Redis cache with 60s TTL.
- **[Trade-off] Fixed layout** → No widget customization. Acceptable — Custom Dashboard Builder (O8) is a future enhancement.
