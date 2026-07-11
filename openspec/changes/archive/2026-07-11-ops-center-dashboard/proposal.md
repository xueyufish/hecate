## Why

Ops Center has three independent sub-dashboards (Tool Analytics 8.9c, Agent Health 8.9a, Conversation Analytics 8.9b), but there is no unified entry point. Operators must visit three separate pages to understand overall system health. No single view answers "is my agent fleet healthy, are tools working, and are conversations quality?" — the three most important operational questions.

Competing platforms (Salesforce Agentforce Command Center, IBM watsonx Runtime Monitoring, Palantir AIP Control Panel) all provide a unified "single pane of glass" that aggregates operational metrics from all subsystems. Hecate needs the same.

This change (feature 8.9) is the fourth and final Ops Center change. It is an aggregation layer — it does not create new data sources. It consumes the three existing Ops Center services (ToolAnalyticsService, AgentHealthService, ConversationAnalyticsService) via a single backend aggregation endpoint (BFF pattern) and renders a unified dashboard.

## What Changes

- **New: `OpsCenterOverviewService`** — Backend aggregation service that fans out to the three existing Ops Center services in parallel (`asyncio.gather`), collects results, and returns a unified overview payload. Handles partial failures gracefully (returns `null` for failed sources with error metadata).
- **New: REST API** — `GET /api/ops-center/overview` returns aggregated metrics from all three subsystems. Single endpoint, single response, structured JSON for the frontend.
- **New: Frontend overview page** — Unified dashboard at `web/src/app/(dashboard)/ops-center/page.tsx` displaying three summary cards (Agent Health, Tool Analytics, Conversation Quality), a cross-source Recent Activity Feed, and quick links to sub-dashboards.
- **Modified: Sidebar** — Change "Ops Center" link from `/ops-center/tools` to `/ops-center` (the new overview page). Keep Agent Health, Tool Analytics, and Conversations as sibling links.
- **New: Recent Activity Feed** — Time-sorted feed of notable events across all three subsystems: critical agents, tool failure spikes, low-quality conversations. Queries existing data sources — no new data infrastructure.

## Capabilities

### New Capabilities

- `ops-center-overview`: Unified Ops Center dashboard aggregating metrics from Agent Health (8.9a), Tool Analytics (8.9c), and Conversation Analytics (8.9b). Backend BFF-style aggregation endpoint with partial failure handling. Frontend overview page with summary cards, recent activity feed, and quick links to sub-dashboards.

### Modified Capabilities

_(none — this change introduces a new aggregation capability without modifying existing spec requirements)_

## Impact

- **Services layer**: New `OpsCenterOverviewService` in `services/ops_center/`. Follows existing pattern — constructor takes `AsyncSession`, calls existing services in parallel.
- **API layer**: New endpoint added to existing `api/management/` (standalone router or added to an existing ops-center router). Registered in `main.py`.
- **Frontend**: New `ops-center/page.tsx` overview page. Modified sidebar link target.
- **Config**: No new settings — uses existing time range defaults.
- **Dependencies**: No new packages — reuses existing FastAPI, SQLAlchemy, React, and chart libraries.
- **Tests**: New test file for OpsCenterOverviewService (aggregation logic, partial failure handling).
