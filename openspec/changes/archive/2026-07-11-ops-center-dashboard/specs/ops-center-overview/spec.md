## ADDED Requirements

### Requirement: Unified overview aggregation endpoint
The system SHALL expose `GET /api/ops-center/overview` that aggregates metrics from all three Ops Center subsystems (Agent Health, Tool Analytics, Conversation Analytics) into a single response. The endpoint SHALL call the three existing services in parallel via `asyncio.gather(return_exceptions=True)`. Supports `start_date` and `end_date` query parameters.

#### Scenario: All three sources available
- **WHEN** a client requests `GET /api/ops-center/overview?start_date=...&end_date=...`
- **THEN** the system returns `{agent_health: {...}, tool_analytics: {...}, conversation_analytics: {...}, errors: []}` with data from all three subsystems

#### Scenario: One source fails
- **WHEN** the ToolAnalyticsService raises an exception during aggregation
- **THEN** the system returns `{agent_health: {...}, tool_analytics: null, conversation_analytics: {...}, errors: ["tool_analytics: <error message>"]}` with HTTP 200 (not 500)

#### Scenario: All sources fail
- **WHEN** all three services raise exceptions
- **THEN** the system returns `{agent_health: null, tool_analytics: null, conversation_analytics: null, errors: [...]}` with HTTP 200

### Requirement: Agent Health summary card
The overview SHALL include an agent health summary with: total agents, healthy count, warning count, critical count, fleet error rate, and fleet P95 latency. This data comes from `AgentHealthService.get_fleet_overview()`.

#### Scenario: Agent health summary displayed
- **WHEN** the overview endpoint returns agent_health data
- **THEN** the frontend displays a card showing total agents, health distribution (healthy/warning/critical counts with color-coded badges), fleet error rate, and fleet P95 latency

#### Scenario: Agent health data unavailable
- **WHEN** agent_health is null in the overview response
- **THEN** the frontend displays "Agent health data unavailable" with a retry indicator

### Requirement: Tool Analytics summary card
The overview SHALL include a tool analytics summary with: total executions, success rate, P95 latency, error count, and unique tools. This data comes from `ToolAnalyticsService.get_overview()`.

#### Scenario: Tool analytics summary displayed
- **WHEN** the overview endpoint returns tool_analytics data
- **THEN** the frontend displays a card showing total executions, success rate percentage, P95 latency, and error count

#### Scenario: Tool analytics data unavailable
- **WHEN** tool_analytics is null in the overview response
- **THEN** the frontend displays "Tool analytics data unavailable" with a retry indicator

### Requirement: Conversation Quality summary card
The overview SHALL include a conversation quality summary with: total conversations, scored conversations, average quality score, and feedback ratio (positive / total). This data comes from `ConversationAnalyticsService.get_overview()`.

#### Scenario: Conversation quality summary displayed
- **WHEN** the overview endpoint returns conversation_analytics data
- **THEN** the frontend displays a card showing total conversations, scored conversations, average quality score, and feedback ratio percentage

#### Scenario: Conversation data unavailable
- **WHEN** conversation_analytics is null in the overview response
- **THEN** the frontend displays "Conversation data unavailable" with a retry indicator

### Requirement: Recent Activity Feed
The overview SHALL include a Recent Activity Feed that surfaces notable events across all three subsystems: critical/warning agents, recent tool errors, and low-quality conversations. The feed SHALL be time-sorted (most recent first) with a maximum of 20 items. Each item SHALL have: timestamp, source (agent_health/tool_analytics/conversation_analytics), severity (critical/warning/info), title, and a link to the relevant sub-dashboard.

#### Scenario: Activity feed with mixed events
- **WHEN** the overview page loads
- **THEN** the feed displays recent events sorted by timestamp: critical agents, tool error spikes, low-quality conversations — each with a severity badge and link to detail

#### Scenario: No recent anomalies
- **WHEN** all subsystems are healthy (no critical agents, no tool errors, no low-quality conversations)
- **THEN** the feed displays "All systems operational" message

#### Scenario: Activity feed limited to 20 items
- **WHEN** more than 20 notable events exist
- **THEN** the feed shows only the 20 most recent items

### Requirement: Quick links to sub-dashboards
The overview page SHALL display quick-link buttons to the three sub-dashboards: Agent Health (`/ops-center/agents`), Tool Analytics (`/ops-center/tools`), Conversations (`/ops-center/conversations`). Each link SHALL open the respective sub-dashboard.

#### Scenario: Quick links displayed
- **WHEN** the overview page is rendered
- **THEN** three link cards are displayed: "View Agent Health", "View Tool Analytics", "View Conversations"

### Requirement: Time range selector
The overview page SHALL include a time range selector (Last 24h / 7d / 30d) that re-fetches the overview data with updated `start_date` and `end_date` parameters.

#### Scenario: Default time range
- **WHEN** the user navigates to the overview page
- **THEN** the default time range is "Last 7 days" and the overview data reflects that range

#### Scenario: Change time range
- **WHEN** the user selects "Last 24h"
- **THEN** the page re-fetches `GET /api/ops-center/overview` with start_date = now - 24h and end_date = now

### Requirement: Sidebar overview link
The sidebar SHALL have an "Ops Center" link pointing to `/ops-center` (the new overview page). The existing sub-dashboard links (Agent Health, Tool Analytics, Conversations) SHALL remain as sibling links.

#### Scenario: Ops Center link points to overview
- **WHEN** the user clicks "Ops Center" in the sidebar
- **THEN** the browser navigates to `/ops-center` (the unified overview page)

#### Scenario: Sub-dashboard links remain accessible
- **WHEN** the sidebar is rendered
- **THEN** "Agent Health" (`/ops-center/agents`), "Tool Analytics" (`/ops-center/tools`), and "Conversations" (`/ops-center/conversations`) links are visible as siblings to "Ops Center"

### Requirement: Empty state handling
The overview page SHALL display an appropriate empty state when no Ops Center data exists for the selected time range.

#### Scenario: No data in time range
- **WHEN** the selected time range has no data from any subsystem
- **THEN** the page displays "No Ops Center data available for this period" with guidance to start using agents to generate data
