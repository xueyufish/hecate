## Purpose

Evaluation report dashboard: on-demand aggregation APIs over existing evaluation data (offline runs/scores and online task scores) plus an ops-center page that visualizes quality, trends, distributions, comparisons, and session-level rollups for the evaluation system.

## Requirements

### Requirement: Overview report endpoint
The system SHALL expose `GET /api/evaluation/reports/overview` that aggregates four card metrics over a time window (query params `start_date`/`end_date`, default last 30 days): quality (pass rate aggregated from completed offline run summaries plus average online task score), volume (completed offline run count plus online scored-trace count), coverage (active dataset count, median items per active dataset, and the low-sample run ratio), and evaluation error rate (fraction of scores with the error sentinel value `-1.0` across both offline scores and online task scores).

#### Scenario: Overview with mixed data
- **WHEN** the workspace has 2 completed offline runs with summaries `pass_rate=0.9` and `pass_rate=0.7`, and 3 online task scores `0.8, 0.6, 1.0`
- **THEN** the overview response reports offline pass rate `0.8`, online average score `0.8`, volume counts, and the four card payloads in one response

#### Scenario: Empty workspace returns zeroed payload
- **WHEN** the workspace has no evaluation data in the window
- **THEN** the endpoint returns 200 with zeroed/empty card payloads (no error)

### Requirement: Coverage card low-sample ratio
The coverage payload SHALL include the fraction of completed offline runs in the window whose dataset has fewer than 20 items ("low-sample run ratio"), counting dataset items at query time.

#### Scenario: Low-sample runs counted
- **WHEN** 3 completed runs exist in the window, 1 of them on a dataset with 8 items and 2 on datasets with 50 items
- **THEN** the low-sample run ratio is `0.3333` (rounded to 4 decimal places)

### Requirement: Workspace isolation on report endpoints
All report endpoints SHALL scope every aggregation to the caller's workspace; scores, runs, tasks, and datasets from other workspaces SHALL NOT be included.

#### Scenario: No cross-workspace leakage
- **WHEN** workspace A requests the overview while workspace B has 5 completed runs
- **THEN** the response reflects only workspace A data

### Requirement: Trends endpoint
The system SHALL expose `GET /api/evaluation/reports/trends` returning per-bucket timeseries for a selectable dimension (`dataset`, `workflow`, or `agent`) and bucket size (`day` or `hour`, default `day`), within the requested window. Offline series aggregate `pass_rate` from completed run summaries bucketed by completion time; online series aggregate average score per metric bucketed by score creation time, restricted to the selected agent when `dimension=agent`.

#### Scenario: Offline pass-rate trend by dataset
- **WHEN** `GET /api/evaluation/reports/trends?dimension=dataset&metric=pass_rate` is called and dataset X has 2 completed runs with `pass_rate=1.0` and `pass_rate=0.5` on the same day
- **THEN** that day's bucket for dataset X reports `0.75`

#### Scenario: Invalid dimension rejected
- **WHEN** `dimension=session` is supplied (not one of `dataset|workflow|agent`)
- **THEN** the endpoint responds 422 with a validation error

### Requirement: Distributions endpoint
The system SHALL expose `GET /api/evaluation/reports/distributions` returning per-metric score histograms over a scope (`run_id` for offline runs, or `task_id`/agent filter for online scores), using 10 equal bins covering `[0.0, 1.0]`. Error scores (`value = -1.0`) SHALL be excluded from histogram bins and reported separately as an error count.

#### Scenario: Histogram excludes error scores
- **WHEN** a run has 4 scores of `0.95` for metric `faithfulness` and 1 error score of `-1.0`
- **THEN** the top bin `[0.9, 1.0]` count is 4 and the response reports `error_count: 1` for that metric

### Requirement: Breakdowns endpoint
The system SHALL expose `GET /api/evaluation/reports/breakdowns` for online task scores with `group_by` (`agent`, `task`, `session`, or `source`) returning per-group per-metric averages and counts, filterable by `metric_name` and the standard window.

#### Scenario: Group by source
- **WHEN** online scores exist from sources `llm_judge` (2 scores, avg 0.8) and `human` (1 score, 0.4)
- **THEN** `group_by=source` returns one row per source with those averages and counts

#### Scenario: Group by agent
- **WHEN** `group_by=agent` is requested and scores link to 2 distinct agents
- **THEN** the response returns one row per agent with per-metric averages

### Requirement: Session rollup endpoint
The system SHALL expose `GET /api/evaluation/reports/sessions` that rolls online task scores up to session granularity: per session, the per-metric average, scored trace count, owning agent, and last-scored time, ordered by last-scored time descending, paginated.

#### Scenario: Session aggregates its trace scores
- **WHEN** session S has 3 scored traces for metric `helpfulness` with values `0.2, 0.6, 1.0`
- **THEN** the session row reports `avg=0.6`, `trace_count=3`, and carries `session_id`/`agent_id` for drill-down

### Requirement: Ops Center evaluation page
The system SHALL provide an evaluation page at `/ops-center/evaluation` with four views: Overview (four summary cards plus a trend chart), Online Quality, Run Report, and Compare. The page SHALL reuse existing Recharts chart components and SHALL show empty states when a view has no data in the selected window.

#### Scenario: Overview renders with data
- **WHEN** the user opens `/ops-center/evaluation` and evaluation data exists
- **THEN** four summary cards (quality, volume, coverage, error rate) and a trend chart render from the overview/trends endpoints

#### Scenario: Empty state
- **WHEN** the workspace has no evaluation data
- **THEN** the page displays a "No evaluation data" empty state with guidance instead of empty charts

### Requirement: Run report view
The Run Report view SHALL let the user select a completed run and display per-metric score distributions, a low-score item list (lowest scores first) where each row reveals the score's `reasoning` on click, and a `source` indicator (`llm_judge` / `deterministic` / `human`) visually distinguishing score origin.

#### Scenario: Drill into a low score
- **WHEN** the user clicks a score row in the low-score list
- **THEN** the row expands to show the evaluator reasoning text and the source badge

### Requirement: Compare view
The Compare view SHALL let the user select two runs and render the existing run-comparison API result: per-metric deltas, paired token/latency/cost deltas, and dataset/node drift indicators.

#### Scenario: Comparison renders deltas
- **WHEN** the user selects a baseline and a candidate run and submits
- **THEN** the view shows per-metric delta values, paired token/latency/cost deltas, and a drift badge when `dataset_drift` is present in the response

### Requirement: Online Quality view
The Online Quality view SHALL display, per online task: a sampling-budget strip (sampled/scored/error counters against the task's configured `sampling_rate` and `max_traces_per_cycle`), an agent × metric score chart, and a session list that navigates to the session drill-down with the session's scores.

#### Scenario: Budget strip from task metrics
- **WHEN** an online task has counters `sampled=120` with `max_traces_per_cycle=50` configured
- **THEN** the strip shows the counters and the configured cap

#### Scenario: Session drill-down
- **WHEN** the user clicks a session in the Online Quality session list
- **THEN** the view shows that session's per-metric scores

### Requirement: Ops Center sidebar navigation entry
The sidebar SHALL include an "Evaluation" navigation entry under the Ops Center section linking to `/ops-center/evaluation`.

#### Scenario: Sidebar shows Evaluation entry
- **WHEN** the dashboard sidebar renders
- **THEN** "Evaluation" appears as an Ops Center navigation item linking to `/ops-center/evaluation`
