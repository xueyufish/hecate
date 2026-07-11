## 1. Backend — OpsCenterOverviewService

- [x] 1.1 Create `src/hecate/services/ops_center/overview.py` with `OpsCenterOverviewService` class (constructor takes `AsyncSession`)
- [x] 1.2 Implement `get_overview(start_date, end_date)` → calls `AgentHealthService.get_fleet_overview()`, `ToolAnalyticsService.get_overview()`, `ConversationAnalyticsService.get_overview()` in parallel via `asyncio.gather(return_exceptions=True)`. Returns unified dict with `agent_health`, `tool_analytics`, `conversation_analytics` (null on failure), and `errors` list.
- [x] 1.3 Implement `get_recent_activity(start_date, end_date, limit=20)` → queries critical/warning agents, recent tool errors, low-quality conversations. Merges and sorts by timestamp. Returns list of activity items with source, severity, title, timestamp, link.

## 2. Backend — Tests

- [x] 2.1 Test `get_overview()` with all three sources available (verify all three sections populated, errors empty)
- [x] 2.2 Test `get_overview()` with one source failing (verify failed section is null, error message in errors list, other sections still populated)
- [x] 2.3 Test `get_overview()` with all sources failing (verify all sections null, errors list populated, HTTP 200 not 500)
- [x] 2.4 Test `get_recent_activity()` with mixed events (verify sorted by timestamp, max 20 items)

## 3. Backend — API

- [x] 3.1 Create `src/hecate/api/management/ops_center_overview.py` router with prefix `/api/ops-center`
- [x] 3.2 Implement `GET /overview` endpoint (start_date, end_date query params) → returns overview dict
- [x] 3.3 Implement `GET /recent-activity` endpoint (start_date, end_date, limit query params) → returns activity list
- [x] 3.4 Register `ops_center_overview_router` in `main.py`

## 4. Frontend — Overview Page

- [x] 4.1 Create `web/src/app/(dashboard)/ops-center/page.tsx` with three summary cards (Agent Health, Tool Analytics, Conversation Quality)
- [x] 4.2 Add Agent Health card (total agents, healthy/warning/critical counts with color-coded badges, fleet error rate, fleet P95 latency)
- [x] 4.3 Add Tool Analytics card (total executions, success rate, P95 latency, error count)
- [x] 4.4 Add Conversation Quality card (total conversations, scored conversations, avg quality score, feedback ratio)
- [x] 4.5 Add partial failure handling: display "Data unavailable" for null sections with retry indicator
- [x] 4.6 Add Recent Activity Feed (time-sorted list with severity badges and links to sub-dashboards)
- [x] 4.7 Add quick-link buttons to sub-dashboards (Agent Health, Tool Analytics, Conversations)
- [x] 4.8 Add time range selector (24h / 7d / 30d) that re-fetches overview data
- [x] 4.9 Add empty state handling ("No Ops Center data available for this period")

## 5. Frontend — Sidebar

- [x] 5.1 Change "Ops Center" sidebar link from `/ops-center/tools` to `/ops-center` (the new overview page)

## 6. Verification

- [x] 6.1 Run `ruff check src/hecate/ tests/ && ruff format --check src/ tests/` — 0 errors
- [x] 6.2 Run `mypy src/` — 0 errors
- [x] 6.3 Run `python -m pytest tests/test_ops_center/test_ops_center_overview.py -q` — all pass
- [x] 6.4 Verify end-to-end: navigate to /ops-center, confirm all three summary cards display, activity feed populated, quick links work
