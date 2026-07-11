## ADDED Requirements

### Requirement: Conversation analytics overview page
The system SHALL provide a React dashboard page at `/ops-center/conversations` displaying conversation analytics: total conversations, scored conversations, average quality score, quality distribution (high/medium/low), and feedback summary.

#### Scenario: Overview page loads with summary cards
- **WHEN** the user navigates to `/ops-center/conversations`
- **THEN** the page fetches `GET /api/ops-center/conversations/overview` and displays summary cards: total conversations, scored conversations, avg quality score, feedback ratio

#### Scenario: Time range filter
- **WHEN** the user selects a different time range (24h / 7d / 30d)
- **THEN** the page re-fetches overview data with updated `start_date` and `end_date` parameters

### Requirement: Quality distribution chart
The system SHALL display a quality score distribution chart showing the histogram of quality scores (buckets: 0.0–0.2, 0.2–0.4, 0.4–0.6, 0.6–0.8, 0.8–1.0). The chart SHALL use color coding: red for low scores (<0.4), yellow for medium (0.4–0.7), green for high (>0.7).

#### Scenario: Quality distribution displayed
- **WHEN** the user views the conversation analytics dashboard
- **THEN** the page displays a bar chart showing the count of conversations in each quality score bucket

#### Scenario: Click bucket to filter conversations
- **WHEN** the user clicks a quality score bucket (e.g., 0.2–0.4)
- **THEN** the page filters the conversation list to show only conversations in that score range

### Requirement: Topic distribution display
The system SHALL display topic distribution as a pie chart or bar chart showing conversation count per topic. Each topic SHALL display its label and average quality score.

#### Scenario: Topic distribution displayed
- **WHEN** the user views the conversation analytics dashboard
- **THEN** the page displays a chart showing conversation count per topic (e.g., "technical_support: 45, billing: 30, unclassified: 25")

#### Scenario: Click topic to filter conversations
- **WHEN** the user clicks a topic in the distribution chart
- **THEN** the page filters the conversation list to show only conversations in that topic

### Requirement: Low-quality conversation list
The system SHALL display a list of conversations with quality_score below a configurable threshold (default 0.5). The list SHALL show conversation ID, agent name, quality score, topic, turn count, and last active time. Clicking a conversation SHALL navigate to the turn-level detail view.

#### Scenario: Low-quality list displayed
- **WHEN** the user views the conversation analytics dashboard
- **THEN** the page displays a table of conversations with quality_score < 0.5, sorted by quality_score ascending

#### Scenario: Click conversation to view details
- **WHEN** the user clicks a conversation row in the low-quality list
- **THEN** the page navigates to a detail view showing turn-level quality scores

### Requirement: Turn-level quality detail view
The system SHALL provide a detail view for individual conversations showing turn-level quality scores. Each turn SHALL display: turn index, user message preview, assistant message preview, helpfulness score, coherence score, instruction_adherence score, overall score, reasoning text, and user feedback (if any).

#### Scenario: Detail view shows turn scores
- **WHEN** the user views the detail page for a conversation with 5 turns
- **THEN** the page displays 5 turn cards, each showing the message previews, quality scores, and reasoning

#### Scenario: Detail view shows user feedback
- **WHEN** a turn has user feedback (positive/negative)
- **THEN** the turn card displays the feedback rating and comment alongside the automated scores

### Requirement: Feedback metrics display
The system SHALL display feedback metrics in the dashboard: total feedback submissions, positive/negative ratio, and feedback trend over time.

#### Scenario: Feedback summary displayed
- **WHEN** the user views the conversation analytics dashboard
- **THEN** the page displays feedback volume (total submissions), feedback ratio (positive / total), and a chart of feedback trends

### Requirement: Sidebar navigation entry
The system SHALL add a "Conversations" sub-navigation item under the existing "Ops Center" section in the sidebar, linking to `/ops-center/conversations`.

#### Scenario: Sidebar displays Conversations link
- **WHEN** the sidebar is rendered
- **THEN** under "Ops Center" section, "Conversations", "Agents", and "Tools" items are all visible as sibling links

### Requirement: Empty state handling
The system SHALL display an empty state message when no conversation data exists for the selected time range or filters.

#### Scenario: No conversations in time range
- **WHEN** the user selects a time range with no conversations
- **THEN** the page displays "No conversation data available for this period" with an illustration

#### Scenario: No scored conversations
- **WHEN** conversations exist but none have been scored yet
- **THEN** the page displays "Quality scores will appear here once conversations are evaluated"
