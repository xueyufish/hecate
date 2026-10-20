## Purpose

Evaluation report dashboard: on-demand aggregation APIs over existing evaluation data (offline runs/scores, online task scores, and human annotation scores), plus an ops-center page that visualizes quality, trends, distributions, comparisons, session-level rollups, human annotation queues, and machine-vs-human calibration. Online aggregations apply a human-override reconciliation rule (latest override replaces the superseded automated value, override creation time is the effective time); the breakdowns endpoint stays raw so `group_by=source` continues to expose both sides.

## Requirements

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

### Requirement: Breakdowns endpoint
The system SHALL expose `GET /api/evaluation/reports/breakdowns` for online task scores with `group_by` (`agent`, `task`, `session`, or `source`) returning per-group per-metric averages and counts, filterable by `metric_name` and the standard window.

#### Scenario: Group by source
- **WHEN** online scores exist from sources `llm_judge` (2 scores, avg 0.8) and `human` (1 score, 0.4)
- **THEN** `group_by=source` returns one row per source with those averages and counts

#### Scenario: Group by agent
- **WHEN** `group_by=agent` is requested and scores link to 2 distinct agents
- **THEN** the response returns one row per agent with per-metric averages

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

### Requirement: Run report view
The Run Report view SHALL let the user select a completed run and display per-metric score distributions, a low-score item list (lowest scores first) where each row reveals the score's `reasoning` on click, and a `source` indicator (`llm_judge` / `deterministic` / `human`) visually distinguishing score origin. The `source` indicator SHALL fall back to rendering an unknown `source` value as its raw string without a special badge style, and SHALL NOT enter a broken render state when that value collides with an object prototype property name (such as `constructor` or `__proto__`).

#### Scenario: Drill into a low score
- **WHEN** the user clicks a score row in the low-score list
- **THEN** the row expands to show the evaluator reasoning text and the source badge

#### Scenario: Unknown source value renders as raw text

- **WHEN** a score row carries a `source` value outside the known set, including names that collide with object prototype properties such as `constructor`
- **THEN** the row renders that raw string as the source indicator without a badge variant, and the view does not crash or render a function/object placeholder

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

### Requirement: Run report 查询状态明确呈现

Run Report 视图的 Run 列表、评分分布与低分列表三路查询 SHALL 各自区分加载中、成功空态与查询失败三种状态。查询失败 SHALL 消费并展示 `detail.error.{code,message}` 错误结构并提供手动重试,SHALL NOT 把失败呈现为"No scores recorded for this run."等空数据语义。评分分布与低分列表 SHALL 相互独立:任一路失败 SHALL 不清除、不掩盖另一路已成功的结果。Run 列表查询失败时 SHALL 在 Run 选择区域呈现错误与重试入口;真实空列表 SHALL 呈现无可选 Run 的空态而非错误。重试 SHALL 按当前选中 Run 与既有取数范围重新发起。

#### Scenario: Run 列表失败与重试

- **WHEN** Run 列表查询返回 403 或 500
- **THEN** Run 选择区域显示错误态、该 `code/message` 与重试按钮,不显示为空 Run 列表;点击重试后重新请求列表并恢复既有行为

#### Scenario: 真实空列表不冒充错误

- **WHEN** Run 列表查询成功且过滤 `completed` 后为空
- **THEN** 界面呈现无可选 Run 的空态,分布与低分列表保持未选择提示,不出现错误态

#### Scenario: 一路失败不清除另一路成功结果

- **WHEN** 评分分布查询失败而低分列表查询成功
- **THEN** 分布区域显示错误态与重试入口,低分列表保持已成功内容;反向(分布成功、评分失败)同理,失败侧不渲染"No scores recorded"文案

#### Scenario: 未选择 Run 不宣称评分为空

- **WHEN** Run 列表尚在加载、查询失败,或过滤 `completed` 后没有可选 Run
- **THEN** 分布与评分区域仅显示选择 Run 的引导提示，不显示“No scores recorded for this run.”，且不发起评分或分布查询

### Requirement: Run 报表查询的迟到响应丢弃

切换选中的 Run SHALL 立即隐藏旧 Run 的分布与低分内容(呈现加载态)并收起已展开的评分行;旧 Run 查询的成功与错误响应 SHALL 一律丢弃,包括 A→B→A 回切后返回的首个 A 请求。重试 SHALL 始终针对当前选中的 Run 发起,SHALL NOT 以旧 Run 的响应更新当前视图。时间窗口参数变化 SHALL 视为对当前选中 Run 的重新查询,并受同样的丢弃约束;评分排序(按 value 升序)与低分列表取数范围 SHALL 保持既有语义。

#### Scenario: 切换 Run 隐藏旧内容并清空展开

- **WHEN** Run A 的分布与低分列表已展示且某评分行处于展开状态,用户切换到 Run B
- **THEN** 旧内容立即被加载态替换,展开行收起,A 的数据不再展示

#### Scenario: 迟到成功与迟到错误均被丢弃

- **WHEN** 切换到 Run B 后,A 的分布或评分响应(无论成功或失败)晚于 B 的响应返回
- **THEN** 该过期响应被丢弃,B 的视图内容与状态不受影响

#### Scenario: A→B→A 回切不串数据

- **WHEN** 用户从 Run A 切到 Run B 再切回 A,A 的首个请求在回切后才返回
- **THEN** 该过期响应被丢弃,视图展示回切后针对 A 重新发起的查询结果

#### Scenario: 重试始终针对当前 Run

- **WHEN** Run B 的分布查询失败后用户点击重试
- **THEN** 重试请求以 Run B 为 `run_id` 重新发起,不使用先前 Run 的标识
