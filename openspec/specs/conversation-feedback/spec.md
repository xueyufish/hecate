## ADDED Requirements

### Requirement: Turn-level user feedback capture
The system SHALL allow users to rate individual assistant turns with a positive or negative rating and an optional comment. Feedback SHALL be stored in the ConversationTurnScoreModel alongside automated quality scores.

#### Scenario: Submit positive feedback for a turn
- **WHEN** a user submits feedback `{rating: "positive", comment: "Great answer!"}` for turn 3 of a conversation
- **THEN** the system updates the ConversationTurnScoreModel record for turn 3 with user_rating="positive", user_comment="Great answer!", feedback_at=current_timestamp

#### Scenario: Submit negative feedback without comment
- **WHEN** a user submits feedback `{rating: "negative"}` for turn 5
- **THEN** the system updates the ConversationTurnScoreModel record for turn 5 with user_rating="negative", user_comment=null

#### Scenario: Overwrite existing feedback
- **WHEN** a user submits feedback for a turn that already has feedback
- **THEN** the system overwrites the previous feedback with the new rating and comment

### Requirement: Feedback API endpoint
The system SHALL expose `POST /api/ops-center/conversations/{id}/turns/{turn_index}/feedback` for submitting turn-level feedback. The endpoint SHALL accept `{rating: "positive"|"negative", comment: str | None}` and return the updated turn score record.

#### Scenario: Submit feedback via API
- **WHEN** a client sends `POST /api/ops-center/conversations/{id}/turns/3/feedback` with body `{rating: "positive", comment: "Helpful"}`
- **THEN** the system returns 200 with the updated ConversationTurnScoreModel record

#### Scenario: Invalid rating value
- **WHEN** a client sends feedback with `rating: "neutral"` (not positive/negative)
- **THEN** the system returns 422 with validation error

#### Scenario: Turn not found
- **WHEN** a client submits feedback for a turn_index that doesn't exist
- **THEN** the system returns 404 with error message

### Requirement: Conversation-level feedback summary
The system SHALL compute a feedback_summary on the ConversationModel aggregating all turn-level feedback: `{positive: count, negative: count, total: count}`. This summary SHALL be updated when feedback is submitted.

#### Scenario: Feedback summary after 3 feedback submissions
- **WHEN** a conversation has 2 positive and 1 negative feedback submissions
- **THEN** conversation.feedback_summary is `{positive: 2, negative: 1, total: 3}`

#### Scenario: Feedback summary updated on new submission
- **WHEN** a user submits new feedback for a turn
- **THEN** the conversation feedback_summary is recomputed to include the new feedback

### Requirement: Feedback display in dashboard
The system SHALL display feedback metrics in the conversation analytics dashboard: feedback volume (total submissions), feedback ratio (positive / total), and feedback trends over time.

#### Scenario: Dashboard shows feedback metrics
- **WHEN** the user views the conversation analytics dashboard
- **THEN** the dashboard displays feedback volume, feedback ratio, and a chart of feedback trends over the selected time range

#### Scenario: Filter by feedback status
- **WHEN** the user filters conversations by feedback status (positive/negative/none)
- **THEN** the dashboard shows only conversations matching the filter
