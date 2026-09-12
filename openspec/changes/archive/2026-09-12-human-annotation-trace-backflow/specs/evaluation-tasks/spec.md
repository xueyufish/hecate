## MODIFIED Requirements

### Requirement: Target-typed score storage and query
The system SHALL persist evaluation scores in a dedicated target-typed score store where each record SHALL contain: `task_id` (nullable — `NULL` identifies a human annotation row not produced by any task), `target_type` (v1: `trace`), `target_id`, `session_id`, `agent_id` (denormalized from the target trace), `metric_name`, `value`, `reasoning`, `source`, `status` (`completed` | `error`), and timestamps. Records with `source="human"` SHALL additionally carry `annotator_id`, an optional `overrides_score_id` (reference to the superseded automated row), and an optional `reason_code`. Uniqueness of automated scoring SHALL be enforced per `(task_id, target_type, target_id, metric_name)` among rows where `task_id` is not null; human annotation rows SHALL be exempt from that constraint. The system SHALL expose `GET /api/evaluation/scores` with pagination and optional filters `task_id`, `target_id`, `session_id`, `metric_name`, `status`, and `source`, and the response SHALL include the human-attribution fields when present. Task-scoped queries (`task_id={id}`) SHALL never return human annotation rows. Online scores SHALL be stored separately from offline run scores; offline run score APIs SHALL be unaffected.

#### Scenario: Query scores by task
- **WHEN** `GET /api/evaluation/scores?task_id={id}` is called
- **THEN** all score records produced by that task SHALL be returned ordered by creation time with pagination, and no `source="human"` rows SHALL appear

#### Scenario: Query scores by session
- **WHEN** `GET /api/evaluation/scores?session_id={id}` is called
- **THEN** all score records whose target trace belongs to that session SHALL be returned

#### Scenario: Filter scores by source
- **WHEN** `GET /api/evaluation/scores?source=human&target_id={id}` is called
- **THEN** only human annotation rows for that target SHALL be returned, each carrying `annotator_id` (and `overrides_score_id`/`reason_code` when it is an override)

#### Scenario: Online scores do not leak into run scores
- **WHEN** `GET /api/evaluation/runs/{run_id}/scores` is called for an offline run
- **THEN** only that run's offline scores SHALL be returned; online task scores and human annotation rows SHALL not appear
