## ADDED Requirements

### Requirement: Quota soft-limit alert type
The system SHALL support `quota_soft_limit_reached` as an AlertType. This alert fires when a quota's `soft_limit` threshold is crossed during post-LLM usage recording.

#### Scenario: Soft limit crossed creates alert event
- **WHEN** a post-LLM recording causes quota usage to cross the soft_limit threshold for the first time in a period
- **THEN** an `AlertEventModel` is created with `alert_type="quota_soft_limit_reached"`, `current_value` set to the utilization percentage, and `severity="warning"`

#### Scenario: Soft limit alert notification dispatched
- **WHEN** the AlertEvaluator processes a `quota_soft_limit_reached` event
- **THEN** it SHALL dispatch notifications through the standard escalation policy, including the quota name and current usage in the message template
