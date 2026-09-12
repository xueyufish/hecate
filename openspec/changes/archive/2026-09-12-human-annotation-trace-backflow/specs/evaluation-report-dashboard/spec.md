## ADDED Requirements

### Requirement: Human override reconciliation
Report aggregations over target-typed scores SHALL use reconciled values: for each `(target_id, metric_name)`, if a human row with `overrides_score_id` exists, the latest such human row's value SHALL replace the overridden automated row's value; the overridden automated row and non-override human rows SHALL NOT additionally contribute to aggregates. Aggregations that reconcile: the overview quality/volume cards, the online trends series, the score distributions histograms, and the session rollup. The breakdowns endpoint SHALL be exempt — it SHALL keep reporting raw per-source values so `group_by=source` continues to expose human and automated rows side by side.

#### Scenario: Overview quality uses the overridden value
- **WHEN** a trace has an automated `helpfulness` score of 0.35 and a human override of 0.9
- **THEN** the overview online average includes 0.9 for that trace and not 0.35

#### Scenario: Non-override human rows do not double count
- **WHEN** a trace has an automated score of 0.8 and a plain human annotation (no override) of 0.8
- **THEN** aggregates count the trace's `helpfulness` value exactly once (the automated row)

#### Scenario: Breakdowns stay raw per source
- **WHEN** `GET /api/evaluation/reports/breakdowns?group_by=source` is called and both an automated (0.35) and an override human (0.9) row exist for the same trace and metric
- **THEN** the response reports both rows under their respective sources

### Requirement: Calibration view
The evaluation page SHALL include a Calibration view that renders, per metric: paired sample count, agreement rate, MAE (numeric metrics) or Cohen's Kappa (categorical/boolean metrics), and the machine × human value heatmap from the calibration endpoint, filterable by agent and metric, with an empty state when no paired samples exist in the window.

#### Scenario: Calibration cards render
- **WHEN** the workspace has 10 paired `helpfulness` samples with `agreement_rate: 0.9` and `mae: 0.05`
- **THEN** the Calibration view shows the pair count, agreement rate, MAE, and the heatmap for the metric

#### Scenario: Empty calibration state
- **WHEN** no paired machine-human samples exist in the selected window
- **THEN** the view displays an empty state with guidance instead of empty charts

## MODIFIED Requirements

### Requirement: Overview report endpoint
The system SHALL expose `GET /api/evaluation/reports/overview` that aggregates four card metrics over a time window (query params `start_date`/`end_date`, default last 30 days): quality (pass rate aggregated from completed offline run summaries plus average online task score computed over reconciled score values), volume (completed offline run count plus online scored-trace count), coverage (active dataset count, median items per active dataset, and the low-sample run ratio), and evaluation error rate (fraction of scores with the error sentinel value `-1.0` across both offline scores and online task scores).

#### Scenario: Overview with mixed data
- **WHEN** the workspace has 2 completed offline runs with summaries `pass_rate=0.9` and `pass_rate=0.7`, and 3 online task scores `0.8, 0.6, 1.0`
- **THEN** the overview response reports offline pass rate `0.8`, online average score `0.8`, volume counts, and the four card payloads in one response

#### Scenario: Override replaces the machine value in quality
- **WHEN** one of the 3 online scores (`0.35`) is overridden by a human row with value `0.9`
- **THEN** the online average score is computed over `0.8, 0.6, 0.9` and the card tooltip notes the reconciliation rule

#### Scenario: Empty workspace returns zeroed payload
- **WHEN** the workspace has no evaluation data in the window
- **THEN** the endpoint returns 200 with zeroed/empty card payloads (no error)

### Requirement: Trends endpoint
The system SHALL expose `GET /api/evaluation/reports/trends` returning per-bucket timeseries for a selectable dimension (`dataset`, `workflow`, or `agent`) and bucket size (`day` or `hour`, default `day`), within the requested window. Offline series aggregate `pass_rate` from completed run summaries bucketed by completion time; online series aggregate average reconciled score value per metric bucketed by score creation time, restricted to the selected agent when `dimension=agent`.

#### Scenario: Offline pass-rate trend by dataset
- **WHEN** `GET /api/evaluation/reports/trends?dimension=dataset&metric=pass_rate` is called and dataset X has 2 completed runs with `pass_rate=1.0` and `pass_rate=0.5` on the same day
- **THEN** that day's bucket for dataset X reports `0.75`

#### Scenario: Online trend uses reconciled values
- **WHEN** a trace scored `0.35` on a day is later overridden by a human row of `0.9` created on a later day
- **THEN** the online series attributes the reconciled value `0.9` to the bucket of the human row's creation time, and the original `0.35` does not appear in any bucket

#### Scenario: Invalid dimension rejected
- **WHEN** `dimension=session` is supplied (not one of `dataset|workflow|agent`)
- **THEN** the endpoint responds 422 with a validation error

### Requirement: Distributions endpoint
The system SHALL expose `GET /api/evaluation/reports/distributions` returning per-metric score histograms over a scope (`run_id` for offline runs, or `task_id`/agent filter for online scores), using 10 equal bins covering `[0.0, 1.0]`. Online histograms SHALL bin reconciled score values; error scores (`value = -1.0`) SHALL be excluded from histogram bins and reported separately as an error count.

#### Scenario: Histogram excludes error scores
- **WHEN** a run has 4 scores of `0.95` for metric `faithfulness` and 1 error score of `-1.0`
- **THEN** the top bin `[0.9, 1.0]` count is 4 and the response reports `error_count: 1` for that metric

#### Scenario: Online histogram bins reconciled values once
- **WHEN** an online trace has an automated score of `0.35` overridden by a human row of `0.9`
- **THEN** the online histogram for that metric counts the trace once in bin `[0.9, 1.0]`

### Requirement: Session rollup endpoint
The system SHALL expose `GET /api/evaluation/reports/sessions` that rolls online task scores up to session granularity using reconciled score values: per session, the per-metric average, scored trace count, owning agent, and last-scored time, ordered by last-scored time descending, paginated.

#### Scenario: Session aggregates its trace scores
- **WHEN** session S has 3 scored traces for metric `helpfulness` with values `0.2, 0.6, 1.0`
- **THEN** the session row reports `avg=0.6`, `trace_count=3`, and carries `session_id`/`agent_id` for drill-down

#### Scenario: Override participates in session average
- **WHEN** one of session S's traces has its `0.2` score overridden to `0.8`
- **THEN** the session average is computed over `0.8, 0.6, 1.0` (`avg≈0.8`)

### Requirement: Ops Center evaluation page
The system SHALL provide an evaluation page at `/ops-center/evaluation` with six views: Overview (four summary cards plus a trend chart), Online Quality, Run Report, Compare, Annotation (queue list and workbench, see the human-annotation capability), and Calibration. The page SHALL reuse existing Recharts chart components and SHALL show empty states when a view has no data in the selected window.

#### Scenario: Overview renders with data
- **WHEN** the user opens `/ops-center/evaluation` and evaluation data exists
- **THEN** four summary cards (quality, volume, coverage, error rate) and a trend chart render from the overview/trends endpoints

#### Scenario: Six views are reachable
- **WHEN** the user opens `/ops-center/evaluation`
- **THEN** tab navigation offers Overview, Online, Runs, Compare, Annotation, and Calibration views

#### Scenario: Empty state
- **WHEN** the workspace has no evaluation data
- **THEN** the page displays a "No evaluation data" empty state with guidance instead of empty charts
