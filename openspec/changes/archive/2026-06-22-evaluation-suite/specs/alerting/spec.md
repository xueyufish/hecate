## ADDED Requirements

### Requirement: Evaluation regression alert type
The system SHALL support `evaluation_regression` as an AlertType. This alert fires when an evaluation run's scores drop significantly below a baseline run, indicating a quality regression.

#### Scenario: Regression detected triggers alert
- **WHEN** a run comparison detects that one or more metrics have regressed beyond the threshold (default 5%)
- **THEN** an `AlertEventModel` is created with `alert_type="evaluation_regression"`, `current_value` set to the average regression delta, and `severity="warning"`

#### Scenario: Evaluation regression alert notification
- **WHEN** the AlertEvaluator processes an `evaluation_regression` event
- **THEN** it SHALL dispatch notifications through the standard escalation policy, including the regressed metric names and delta values in the message template
