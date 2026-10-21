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

The Compare view SHALL let the user select two runs and render the existing run-comparison API result: per-metric deltas, paired token/latency/cost deltas, and dataset/node drift indicators. The Run list SHALL distinguish loading, a genuine empty state, and query failure: failure SHALL consume the backend `detail.error.{code,message}` structure (and a plain string `detail` such as the comparison endpoint's 422 responses) and offer a retry, while a completed-filtered empty list SHALL render as a no-selectable-run empty state instead of an error. The comparison area SHALL distinguish not-submitted, in-flight, succeeded, and failed states: a failure SHALL show the backend `code/message` with a retry using the current selection instead of a generic fallback message, and Compare SHALL be disabled with an explicit prompt while the two selected runs are identical.

#### Scenario: Comparison renders deltas
- **WHEN** the user selects a baseline and a candidate run and submits
- **THEN** the view shows per-metric delta values, paired token/latency/cost deltas, and a drift badge when `dataset_drift` is present in the response

#### Scenario: Run list failure and retry

- **WHEN** the Run list query fails with the platform error envelope
- **THEN** the selection area shows the error `code/message` with a retry button instead of an empty list; retry re-issues the list query

#### Scenario: Structured and string error details are both consumed

- **WHEN** a comparison returns 404/403 with `detail.error.{code,message}`, or 422 with a plain string `detail` (such as runs referencing different datasets)
- **THEN** the comparison area shows the corresponding `code/message` or string text, not the generic "Comparison failed" fallback, with a retry for the current selection

#### Scenario: Identical runs disable comparison

- **WHEN** the user selects the same run as both baseline and candidate
- **THEN** the Compare button is disabled and an explicit same-run prompt is shown; no comparison request is issued

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

### Requirement: Compare 查询绑定有序 Run 对并丢弃迟到响应

比较结果 SHALL 绑定发起比较时的有序 Run 对(baseline, candidate)。baseline 或 candidate 改变 SHALL 立即清除旧比较结果与旧错误、使在途比较请求失效,并以未提交状态替换当前内容;旧请求的成功与错误响应 SHALL 一律丢弃,包括 A/B→A/C→A/B 回切后首个请求的迟到响应。新一次比较进行期间 SHALL 隐藏上一次比较结果(呈现比较中状态);失败后的重试 SHALL 使用当前选中的 Run 对重新发起。Run 列表查询 SHALL 支持手动重试;既有成功路径语义(逐指标 delta、回归判定徽标、配对 deltas 与 drift 展示)SHALL 保持不变。

#### Scenario: 选择变化清除旧结果

- **WHEN** Run 对 (A, B) 的比较结果已展示,用户改变 baseline 或 candidate
- **THEN** 旧结果与旧错误立即消失,比较区回到未提交状态,不残留 (A, B) 的内容

#### Scenario: 迟到成功与迟到错误均被丢弃

- **WHEN** 用户为 (A, B) 发起比较后改变选择,该在途请求晚于新状态返回
- **THEN** 无论成功或失败,该过期响应被丢弃,当前选择的内容与状态不受影响

#### Scenario: 回切后首个请求的迟到响应被丢弃

- **WHEN** 用户从 (A, B) 切到 (A, C) 再切回 (A, B),(A, B) 的首个比较请求在回切后才返回
- **THEN** 该过期响应被丢弃,视图展示回切后针对 (A, B) 重新发起的请求结果

#### Scenario: 重试使用当前 Run 对

- **WHEN** (A, C) 的比较失败后用户点击重试
- **THEN** 重试请求以 (A, C) 为 baseline/candidate 重新发起,不使用先前 Run 对

#### Scenario: 同值重选保持当前比较与重试

- **WHEN** 比较或失败后的重试仍在进行，用户重新选择 baseline 或 candidate 当前已选中的同一个 Run
- **THEN** 有序 Run 对没有变化，当前请求保持有效，不重发、不重置提交状态；响应返回后正常呈现成功或错误，不停留在比较中

#### Scenario: 同一 Run 对重复提交仅接受最新响应

- **WHEN** 同一有序 Run 对的前一比较尚未返回，用户再次点击 Compare，新请求先完成
- **THEN** 呈现新请求结果，前一请求的迟到成功和错误均不能覆盖它

#### Scenario: 交换 baseline 与 candidate 是不同 Run 对

- **WHEN** 用户已比较 (A,B)，随后将选择交换为 (B,A)
- **THEN** 旧结果清除，回到未提交状态；手动提交时按 (B,A) 原有序提交，不复用 (A,B) 的结果

#### Scenario: 列表字符串或客户端错误保留原因

- **WHEN** Run 列表失败返回字符串 detail，或 API client 提供顶层 error.code/message
- **THEN** 列表错误区展示对应原因和可用的 code，不降级成成功空列表或丢失原始 message

### Requirement: Distributions report access boundary

`GET /api/evaluation/reports/distributions` SHALL enforce one access boundary for both scopes. The request SHALL first keep its existing parameter contract: a malformed UUID stays a 422, and providing both or neither of `run_id`/`task_id` stays a 400 with the existing message. When exactly one scope is supplied, the referenced Run or evaluation task SHALL be resolved before any role decision: a resource that does not exist, is soft-deleted, or belongs to another workspace SHALL produce the same constant not-found (404) response across both Run and evaluation-task scopes whose body SHALL NOT echo any resource identifier. The caller SHALL then hold a minimum `viewer` role in the resource's workspace; a role-less member of a visible resource's workspace SHALL receive 403, and a non-system identity without an active workspace SHALL be denied before resource lookup or aggregation. Explicit system-scope identities SHALL keep cross-workspace reads, and the service call SHALL use the resolved resource's real workspace — the boundary SHALL NOT introduce a global aggregation query, and system callers SHALL receive the resource's real histograms rather than an empty result. The boundary SHALL NOT modify any data and SHALL NOT change histogram binning, error-score exclusion, human-override reconciliation, or successful response shapes.

#### Scenario: Run scope honors the viewer floor
- **WHEN** a viewer-role member of a run's workspace requests distributions for that run
- **THEN** the response SHALL be 200 with the run's per-metric histograms, unchanged in shape

#### Scenario: Task scope honors the viewer floor
- **WHEN** a viewer-role member of an evaluation task's workspace requests distributions for that task
- **THEN** the response SHALL be 200 with reconciled online histograms, unchanged in shape

#### Scenario: Role-less member of a visible resource is denied
- **WHEN** a membership identity without a role requests distributions for a run or task in its own workspace
- **THEN** the response SHALL be 403 authorization denied and no aggregation SHALL run

#### Scenario: Invisible resources share one identifier-free 404
- **WHEN** the requested run or task does not exist, is soft-deleted, or belongs to another workspace
- **THEN** the response SHALL be 404 with one constant body for all conditions, and the body SHALL NOT contain the run, task, or caller identifier

#### Scenario: System scope reads with the resource's real workspace
- **WHEN** an explicit system-scope identity requests distributions for a run or task in any workspace
- **THEN** the response SHALL be 200 with that resource's real histograms, and the service SHALL be invoked with the resource's workspace rather than a global or null filter

#### Scenario: Missing-workspace non-system identity is denied
- **WHEN** a validly authenticated non-system identity without an active workspace calls the endpoint with exactly one valid scope
- **THEN** the response SHALL be the constant identifier-free 404 and no resource lookup or aggregation query SHALL run

#### Scenario: Parameter contract and read-only guarantee are preserved
- **WHEN** a request supplies both `run_id` and `task_id`, or neither, or a malformed UUID
- **THEN** the responses keep the existing 400 messages and 422 respectively; successful and rejected requests SHALL NOT create, modify, or delete any data, and binning, error-score exclusion, and reconciliation semantics remain unchanged

#### Scenario: Resource disappears after the ownership precheck
- **WHEN** the report service reports that a scope is not found after the router has authorized that scope
- **THEN** the endpoint SHALL return the same constant identifier-free 404 body used for a failed resource precheck, for both Run and evaluation-task scopes

### Requirement: Aggregate report access boundary

The workspace-aggregate report endpoints (`GET /api/evaluation/reports/overview`, `GET /api/evaluation/reports/trends`, `GET /api/evaluation/reports/breakdowns`) SHALL enforce one shared access boundary before any service call: a caller with an active workspace SHALL hold a minimum `viewer` role, and a membership identity whose role is below viewer (or absent) SHALL receive 403 `FORBIDDEN`. A caller without an active workspace — including explicit system-scope identities, because these endpoints take no target-resource or workspace-selection parameter — SHALL receive 403 `NO_WORKSPACE`, and the boundary SHALL NOT add a global aggregation or workspace-selection capability for them. The service SHALL only ever receive the verified active workspace identifier; no zero-UUID or empty-workspace fallback SHALL be introduced. A valid workspace with no data SHALL keep returning a normal empty report. The boundary SHALL NOT modify any data and SHALL NOT change successful response shapes, time-window filters, bucketing, metric filtering, error-score handling, the overview/trends human-override reconciliation, the breakdowns raw per-source statistics, or the distributions and sessions endpoints' own boundaries.

#### Scenario: Viewer, editor, and admin read their workspace aggregates
- **WHEN** an identity holding viewer, editor, or admin role in its active workspace calls overview, trends, or breakdowns
- **THEN** each endpoint SHALL return 200 with its existing response shape for that workspace's data

#### Scenario: Role-less member of a workspace is denied
- **WHEN** a membership identity without a role calls any of the three endpoints
- **THEN** the response SHALL be 403 `FORBIDDEN` and no aggregation SHALL run

#### Scenario: Missing workspace is 403 NO_WORKSPACE including system
- **WHEN** a validly authenticated identity without an active workspace — including an explicit system-scope identity — calls any of the three endpoints
- **THEN** the response SHALL be 403 with error code `NO_WORKSPACE`, no aggregation SHALL run, and no global-aggregation or workspace-selection parameter SHALL be introduced

#### Scenario: Empty workspace keeps normal empty reports
- **WHEN** a viewer-or-higher caller's verified workspace contains no evaluation data
- **THEN** each endpoint SHALL return its existing empty-report response (zeroed overview, empty series/breakdowns) rather than an error

#### Scenario: Boundary preserves aggregation semantics and read-only guarantee
- **WHEN** requests succeed or are rejected by the boundary
- **THEN** no data SHALL be created, modified, or deleted; time-window filters, bucketing, metric filtering, and error-score handling remain unchanged; overview and trends keep the human-override reconciliation, breakdowns keeps raw per-source rows, and the distributions and sessions endpoints keep their own boundaries
