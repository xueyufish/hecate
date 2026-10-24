# Capability: evaluation-tasks

> Synced from archive of change `online-offline-evaluation-tasks` (7.2c).
> Reusable evaluation tasks: persistent offline tasks that run dataset ×
> evaluator batches asynchronously (optionally invoking the agent under
> test), and always-on online tasks that sample completed production traces
> and auto-score them. Scores land in a target-typed store; the online
> worker consumes persisted traces/events only — the runtime request path
> is untouched.

## Purpose

Defines reusable evaluation tasks: persistent offline tasks that run dataset × evaluator batches asynchronously (optionally invoking the agent under test), and always-on online tasks that sample completed production traces and auto-score them with the registered evaluators. Scores land in a target-typed score store queryable by task, target, and session.

## Requirements

### Requirement: Evaluation task definition and CRUD
The system SHALL provide CRUD endpoints at `/api/evaluation/tasks` for evaluation tasks. A task SHALL have a `task_type` of `offline` or `online`. An offline task SHALL reference a `dataset_id` and a non-empty list of registered evaluator names, with optional `answer_source` (`manual` | `pipeline` | `agent`, default `manual`), optional `threshold`, and optional `baseline_run_id`. An online task SHALL reference an `agent_id` filter, a non-empty list of registered evaluator names, and a `sampling_rate` in `(0, 1]`. All tasks SHALL be scoped to the creating workspace and invisible to other workspaces. Tasks SHALL have a `status` of `active` or `disabled` (new tasks default to `active`; only online tasks use `disabled` semantics to stop scoring).

#### Scenario: Create offline task
- **WHEN** `POST /api/evaluation/tasks` is called with `{"task_type": "offline", "name": "nightly-regression", "dataset_id": "...", "evaluators": ["correctness", "faithfulness"], "answer_source": "agent", "threshold": 0.7}`
- **THEN** the task SHALL be persisted with status `active` and returned with its `id`, `task_type`, and resolved configuration

#### Scenario: Reject online task with invalid sampling rate
- **WHEN** an online task is created with `sampling_rate` of `0` or greater than `1`
- **THEN** the API SHALL return a validation error and SHALL NOT persist the task

#### Scenario: Reject task referencing unknown evaluator
- **WHEN** a task is created with an evaluator name that is not registered
- **THEN** the API SHALL return a validation error naming the unresolvable evaluator

#### Scenario: Workspace isolation
- **WHEN** a task belonging to workspace A is requested from workspace B
- **THEN** the API SHALL behave as if the task does not exist

### Requirement: Offline task run execution
The system SHALL expose `POST /api/evaluation/tasks/{task_id}/runs` that queues an asynchronous run of the task's evaluators against the task's dataset and returns `202` with a run identifier in `pending` state. The run SHALL execute in the background with the lifecycle `pending → running → completed | failed`, and SHALL be persisted as an evaluation run linked to the task via `task_id`. When `answer_source` is `manual`, the engine SHALL score the item's stored `generated_answer`. When `answer_source` is `pipeline` or `agent` and the item has no stored answer, the engine SHALL generate the answer — via the RAG pipeline fallback or by invoking the agent under test (one invocation per item), respectively. When `threshold` is configured, the completed run SHALL compute per-item pass/fail and aggregate `summary` (`total_items`, `passed_items`, `failed_items`, `pass_rate`, `metric_averages`); when `baseline_run_id` is configured, the summary SHALL additionally flag regressed metrics against the baseline.

Triggering SHALL enforce a task-level access boundary before any task validation, run creation, persistence commit, or background handoff: for non-system identities, a task that does not exist, is soft-deleted, or belongs to another workspace SHALL produce one constant not-found (404) response that does not echo identifiers, and ownership SHALL be resolved before the minimum editor role so a role denial never discloses task existence. A non-system identity without an active workspace SHALL receive that same constant task not-found without querying the task. Explicit system-scope API keys SHALL trigger by task ID across workspaces, with or without an active workspace. Every denial SHALL occur before the background runner is handed off, and rejected requests SHALL leave existing task, run, and score rows unchanged. A successful trigger SHALL persist the pending run bound to the task's identity, dataset, evaluator configs, and the task's real workspace; the request-session commit SHALL precede the background handoff, and a failed commit SHALL NOT hand off. Authorized requests SHALL keep the existing 400 INVALID_REQUEST branch for online tasks, disabled tasks, and tasks with unresolvable evaluator configs — without creating a run or handing off.

#### Scenario: Trigger asynchronous offline run
- **WHEN** `POST /api/evaluation/tasks/{task_id}/runs` is called on an active offline task
- **THEN** the API SHALL return `202` with a run identifier in `pending` state, and the run SHALL transition to `completed` (or `failed`) without requiring the HTTP request to stay open

#### Scenario: Invisible task yields uniform not-found before role denial
- **WHEN** any authenticated non-system identity triggers a run for a task that does not exist, is soft-deleted, or belongs to another workspace, regardless of the caller's workspace role
- **THEN** the response SHALL be 404 with one consistent body across all conditions, without echoing the task ID, and no run SHALL be created or handed off

#### Scenario: Missing workspace identity receives constant task not-found
- **WHEN** a validly authenticated non-system identity without an active workspace triggers a run
- **THEN** the response SHALL be the same 404 as a missing task, the task SHALL NOT be queried, and no run SHALL be created or handed off

#### Scenario: Editor role required in the task's workspace
- **WHEN** a viewer or role-less member of the task's workspace triggers a run, even for a task whose type, status, or evaluator config would fail validation
- **THEN** the response SHALL be 403 authorization denied (preceding task validation) and no run SHALL be created or handed off

#### Scenario: System scope triggers across workspaces
- **WHEN** an explicit system-scope API key, with or without an active workspace different from the task's, triggers a run by task ID
- **THEN** the trigger SHALL proceed and the pending run SHALL land in the resolved task's workspace

#### Scenario: Pending run persists with task binding before background handoff
- **WHEN** an editor or admin of the task's workspace triggers a run successfully
- **THEN** the persisted pending run SHALL carry the task's identity, dataset, evaluator configs, and the task's real workspace; the background handoff SHALL receive exactly the persisted run and task IDs, happen only after the request-session commit succeeds, and be skipped when the commit fails; a same-workspace viewer SHALL read the run back through the task-run listing and run detail endpoints while members of other workspaces SHALL NOT observe it

#### Scenario: Authorized trigger of invalid task is rejected without side effects
- **WHEN** an editor of the task's workspace triggers a run for an online task, a disabled task, or a task whose persisted configuration fails validation (including unresolvable evaluator names or missing required fields)
- **THEN** the response SHALL be 400 INVALID_REQUEST with the existing message semantics, and no run SHALL be created or handed off

#### Scenario: Agent answer source invokes the agent under test
- **WHEN** a task with `answer_source: "agent"` runs against items lacking `generated_answer`
- **THEN** the engine SHALL invoke the agent under test once per item with the item query, score the returned answer, and record failures of the agent invocation as error scores (`value = -1.0`) without failing sibling items

#### Scenario: Threshold produces pass/fail summary
- **WHEN** a run of a task with `threshold` configured reaches `completed`
- **THEN** the run SHALL carry a `summary` with `total_items`, `passed_items`, `failed_items`, `pass_rate`, and `metric_averages`

#### Scenario: Baseline comparison flags regressions
- **WHEN** a task with `baseline_run_id` completes a run where a metric average dropped more than the threshold delta versus the baseline run
- **THEN** the summary SHALL mark that metric as regressed

### Requirement: Online task lifecycle
The system SHALL expose `POST /api/evaluation/tasks/{task_id}/enable` and `POST /api/evaluation/tasks/{task_id}/disable` for online tasks. Enabling SHALL validate that all referenced evaluators are registered and that `sampling_rate` is in `(0, 1]`; a valid enabled task SHALL score production traces continuously until disabled. Disabling SHALL stop new sampling and scoring while preserving already-produced scores. Enabling or running SHALL be rejected for tasks in `disabled` state.

#### Scenario: Enable online task
- **WHEN** `enable` is called on a valid online task
- **THEN** the task status SHALL become `active` and production traces SHALL begin to be sampled and scored

#### Scenario: Disable stops scoring
- **WHEN** `disable` is called on an enabled online task
- **THEN** the task SHALL stop sampling new traces and previously produced scores SHALL remain queryable

#### Scenario: Enable with unregistered evaluator is rejected
- **WHEN** `enable` is called on an online task referencing an evaluator name not in the registry
- **THEN** the API SHALL return a validation error and the task SHALL remain `disabled`

### Requirement: Online trace sampling and scoring
For each enabled online task, the system SHALL asynchronously discover completed root traces (trace records of type `trace` with `status` `completed`) created after the task's last scan position, filter them by the task's `agent_id`, select a deterministic sample based on `sampling_rate` (the same trace ID yields the same sample decision), and cap each scan cycle at `max_traces_per_cycle` traces. For each sampled trace, the system SHALL build an evaluation input from the session's persisted execution events within the trace's time window — the user query and generated answer from the final user/assistant messages in the window, preceding messages as conversation history, and tool calls from the window's tool-call records — and produce one score record per evaluator metric with `reasoning` preserved. Scoring SHALL be idempotent per `(task_id, target_type, target_id, metric_name)` — re-scanning SHALL NOT duplicate score records. An evaluator failure on one trace SHALL be recorded as an error score (`value = -1.0`, status `error`) and SHALL NOT halt scoring of other traces.

#### Scenario: Sampled trace is scored
- **WHEN** an online task with `sampling_rate: 1.0` and evaluator `relevancy` is enabled and a root trace for the filtered agent completes
- **THEN** a score record for `relevancy` SHALL be persisted with `target_type: "trace"`, the trace's `target_id`, and the trace's `session_id`

#### Scenario: Deterministic sampling controls volume
- **WHEN** an online task with `sampling_rate: 0.1` processes a stream of trace IDs
- **THEN** the same trace ID SHALL always produce the same sample decision, and approximately the configured fraction of traces SHALL be scored

#### Scenario: Scan cycle volume cap
- **WHEN** more than `max_traces_per_cycle` candidate traces are pending in one scan cycle
- **THEN** at most `max_traces_per_cycle` traces SHALL be scored in that cycle and the remainder SHALL be picked up in subsequent cycles

#### Scenario: Duplicate scan does not duplicate scores
- **WHEN** the same completed trace is encountered again by the scanner after already being scored by the same task
- **THEN** no additional score records SHALL be created for that `(task_id, target_id, metric_name)` triple

#### Scenario: Evaluator error is isolated
- **WHEN** an evaluator raises an error while scoring a sampled trace
- **THEN** an error score with `value = -1.0` and status `error` SHALL be recorded with the failure reason, and other traces and evaluators SHALL continue to be scored

### Requirement: Target-typed score storage and query
The system SHALL persist evaluation scores in a dedicated target-typed score store where each record SHALL contain: `task_id` (nullable — `NULL` identifies a human annotation row not produced by any task), `target_type` (v1: `trace`), `target_id`, `session_id`, `agent_id` (denormalized from the target trace), `metric_name`, `value`, `value_label` (categorical/boolean annotations store the label string; numeric rows leave it null), `reasoning`, `source`, `status` (`completed` | `error`), and timestamps. Records with `source="human"` SHALL additionally carry `annotator_id`, an optional `overrides_score_id` (reference to the superseded automated row), and an optional `reason_code`. Uniqueness of automated scoring SHALL be enforced per `(task_id, target_type, target_id, metric_name)` among rows where `task_id` is not null; human annotation rows SHALL be exempt from that constraint. The system SHALL expose `GET /api/evaluation/scores` with pagination and optional filters `task_id`, `target_id`, `session_id`, `metric_name`, `status`, and `source`, and the response SHALL include the human-attribution fields when present. Task-scoped queries (`task_id={id}`) SHALL never return human annotation rows. Online scores SHALL be stored separately from offline run scores; offline run score APIs SHALL be unaffected.

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

### Requirement: Task run listing
The system SHALL expose `GET /api/evaluation/tasks/{task_id}/runs` returning the task's runs with pagination, each entry containing the run identifier, lifecycle `status`, `started_at`/`completed_at`, and `summary` when computed.

#### Scenario: List runs of an offline task
- **WHEN** `GET /api/evaluation/tasks/{task_id}/runs` is called for an offline task with prior runs
- **THEN** the response SHALL list those runs in reverse creation order with their status and summary

### Requirement: Agent 评测运行绑定已提交 AgentVersion

离线 answer_source=agent 的任务 SHALL 支持声明 agent_version。Run SHALL 在实际开始时解析已提交版本并保存自身的 agent_id、agent_version、agent_config_hash 和执行快照；后续任务或草稿修改 SHALL NOT 改变该证据归属。绑定运行 SHALL 按快照的 persona、模型、工具/技能名单、技能 pin、知识库引用及模式执行，workflow 模式 SHALL 使用 pin 的 workflow 版本。绑定评测 SHALL 实际生成答案而非复用预存答案；缺少数据项、版本/资源不可解析、调用失败或跨 workspace 引用 SHALL 显式失败，不静默回退草稿。未声明版本 SHALL 保留既有 live 行为；历史 NULL 运行 SHALL NOT 回填。POST /runs 的现有答题来源范围不变。

#### Scenario: 绑定版本按冻结快照实际执行

- **WHEN** 任务绑定 v2，草稿和已 pin 技能内容随后变化，数据项已有预存答案
- **THEN** Run 保存 v2 的身份、哈希和快照，并实际调用 v2 配置和 pin 技能产生答案，不把预存答案当作 v2 的执行结果

#### Scenario: 模式和 workflow pin 生效

- **WHEN** v2 为 workflow 模式且 pin workflow v1，草稿随后改为 chat
- **THEN** 绑定评测仍通过已有入口执行 workflow v1，不改成 chat 或 workflow latest

#### Scenario: 任务修改不污染历史身份

- **WHEN** 已完成的 v2 Run 对应任务改为另一 Agent/版本或被删除
- **THEN** Run 的 Agent、版本、哈希和快照不变，不能被另一个 Agent 的发布门禁借用

#### Scenario: 任务部分更新保留或显式清除声明

- **WHEN** 任务修改阈值、仅修改 agent_version 或显式传 agent_version=null
- **THEN** 未传版本时保留声明，仅传版本时更新声明，显式 null 清除未来运行的声明，旧 Run 均不改写

#### Scenario: 历史和未声明版本保持原状

- **WHEN** 历史未绑定 Run 被读取，或任务未声明 agent_version
- **THEN** 历史运行不被回填，未声明任务按既有 live/预存答案语义执行，均不冒充版本绑定证据

#### Scenario: 不可解析或跨 workspace 引用失败

- **WHEN** 版本或 Agent 不存在/已删除，或者任务、Run、数据集或被引用资源来自不允许的 workspace
- **THEN** 绑定运行失败，不回退 live，不成为已完成版本评测证据

#### Scenario: 终止运行不可重新绑定

- **WHEN** 已终止 Run 被请求重跑，或已有绑定被请求换成另一版本/未声明版本
- **THEN** 请求被拒绝，原有绑定证据保持不变

### Requirement: 绑定运行冻结评测范围

绑定 AgentVersion 的评测运行 SHALL 在实际样本和豁免确定后、答题前把版本化 evaluation_scope 保存到 Run：schema_version、实际 item_ids、当前受支持内置确定性 required_metrics、范围内 exempted_item_ids 和 dataset_snapshot_hash。tags 和快照/live known-bad 来源 SHALL 按既有语义结清。后续任务、数据集、草稿变更 SHALL NOT 改写范围；重入时范围冲突 SHALL 失败并保留原证据。未绑定运行保持既有行为，历史运行 SHALL NOT 补造范围。

#### Scenario: 范围在生成答案前冻结

- **WHEN** 实际选中十项，其中一项在快照中 known-bad，运行同时配置 exact_match 和 LLM judge
- **THEN** 答题前保存十个 item_ids、一个豁免 id 和 exact_match 必需指标，LLM judge 不进入必需指标

#### Scenario: tags 与快照豁免决定范围

- **WHEN** live 路径按 tags 只选中部分项，快照 known-bad 与活行不一致
- **THEN** 范围只包含实际选中项，豁免依据快照且限制为选中项的子集

#### Scenario: 配置变化不覆盖已保存范围

- **WHEN** 相同 Run 重入但 tags、样本或必需指标改变
- **THEN** 执行在生成答案前失败，原 evaluation_scope 保持不变

#### Scenario: 未绑定与历史保持原状

- **WHEN** live 评测未声明版本，或读取已有无范围运行
- **THEN** live 行为不变，历史不回填为完整证据

### Requirement: 覆盖完整性按冻结范围逐项核验

每个非豁免样本 × 必需确定性指标 SHALL 具备本 Run、本 workspace、未删除、source=deterministic 且数值有效的分数。异常、空输出、缺行、负分、无效值或其他来源 SHALL NOT 填补确定性缺口。覆盖按 pair 去重，重复分数 SHALL NOT 填补另一指标。缺证据与有效低分 SHALL 分别报告。缺失、畸形、未知版本范围、零必需样本/指标或全部豁免 SHALL NOT 空转通过。质量分数 SHALL 仅取冻结非豁免范围；共享 Workflow 评分行为不变。

#### Scenario: 完整分数通过覆盖检查

- **WHEN** 所有必需 pair 均有有效确定性分数
- **THEN** 覆盖通过，再按质量阈值判断，有效低分不会被当成缺证据

#### Scenario: 人工或 judge 分数不能填缺口

- **WHEN** exact_match 的确定性结果缺失，但同名 human 或 llm_judge 分数存在
- **THEN** 该 pair 仍缺失，其他高分和重复分数也不能补齐

#### Scenario: evaluator 失败或空输出不放行

- **WHEN** 十项中一项 evaluator 抛错或返回空输出，其他九项全过
- **THEN** 保留逐项失败隔离，但覆盖缺一项，整体门禁不得以剩余九项高分通过

#### Scenario: 范围不可核验则报告失败

- **WHEN** 范围缺字段、格式无效、版本未知、零必需指标或全部样本豁免
- **THEN** 覆盖报告明确失败原因，不返回成功或因解析失败产生 HTTP 500

#### Scenario: 豁免和越界分数不改变质量

- **WHEN** 豁免样本有低分，或分数行包含范围之外的样本/指标
- **THEN** 这些行不参与本次候选的质量通过率及回归平均分

#### Scenario: 缺失计数完整且定位有界

- **WHEN** 超过二十个 pair 缺失
- **THEN** 报告保留真实 missing_count，并仅展示前二十个定位

### Requirement: Evaluation task read access boundary

The task listing, detail, and run-listing endpoints (`GET /api/evaluation/tasks`, `GET /api/evaluation/tasks/{task_id}`, `GET /api/evaluation/tasks/{task_id}/runs`) SHALL enforce one shared read access boundary: an authenticated non-system identity SHALL only observe evaluation tasks belonging to its active workspace, with a minimum viewer role in that workspace; the listing's items and `total` SHALL count only tasks within the caller's workspace. A task SHALL be readable only when it exists, is not soft-deleted, and belongs to the caller's workspace; a task failing any of these conditions SHALL produce a 404 whose body is identical for all conditions and SHALL NOT echo any resource identifier, and the runs of an invisible task SHALL NOT be listable. The task's run listing SHALL include only runs bound to that task AND carrying the task's workspace, excluding soft-deleted runs; `items` and `total` SHALL apply the same conditions. Ownership SHALL be checked before role so that a cross-workspace caller receives not-found rather than a role denial that discloses existence. Explicit system-scope identities SHALL read across workspaces — their `workspace_id=None` SHALL NOT be used as an equality query condition — and when a system-scope identity reads a task's runs, the runs SHALL be constrained to that task's real workspace. A non-system identity without an active workspace SHALL be denied and SHALL NOT reach an unscoped query. The boundary SHALL NOT change successful response shapes, pagination, task type/status filters, ordering, or the `get_task`/`_get_task_or_raise` scope semantics shared with reads and writes, and the three endpoints SHALL remain read-only.

#### Scenario: Listing is scoped to the caller workspace
- **WHEN** tasks exist in workspaces W1 and W2 and a W1 member calls `GET /api/evaluation/tasks`
- **THEN** only W1 tasks are returned and `total` counts only W1 tasks

#### Scenario: System scope reads across workspaces
- **WHEN** an explicit system-scope identity calls the task listing for tasks in any workspace, or reads a specific task and its runs in any workspace
- **THEN** the requests are processed under the endpoints' existing semantics without workspace restriction from this boundary; the system identity's null workspace SHALL NOT be applied as an equality condition that yields an empty list or not-found

#### Scenario: Missing workspace non-system identity is denied
- **WHEN** a validly authenticated non-system identity without an active workspace calls any of the three task read endpoints
- **THEN** the request is denied and the response SHALL NOT fall back to an unscoped query

#### Scenario: Detail for an invisible task is indistinguishable and echoes no ID
- **WHEN** a caller requests a task that does not exist, is soft-deleted, or belongs to another workspace
- **THEN** all conditions SHALL receive the same 404 response body without echoing any task, workspace, or caller identifier

#### Scenario: Runs of an invisible task are not listable
- **WHEN** a caller requests the run listing of a task that does not exist, is soft-deleted, or belongs to another workspace
- **THEN** the response SHALL be 404 with the same body for all conditions, without echoing any identifier

#### Scenario: Run listing is constrained to the task's real workspace
- **WHEN** a run row references the task but carries a different workspace than the task, alongside runs correctly bound to the task
- **THEN** the mismatched-workspace run SHALL NOT appear in `items` and SHALL NOT be counted in `total`

#### Scenario: Role floor applies after ownership
- **WHEN** an authenticated identity without a workspace role requests a task that belongs to another workspace
- **THEN** the response SHALL be the same 404 as a missing task, not a role-based 403; a viewer-role member of the task's workspace receives the same successful response a higher role would receive

#### Scenario: Role floor inside the caller workspace
- **WHEN** an identity whose active workspace contains the task but whose workspace membership role is below viewer (or absent) calls any of the three task read endpoints
- **THEN** the response SHALL be 403 authorization denied and no task or run content SHALL be returned

#### Scenario: Boundary preserves read semantics and the read-only guarantee
- **WHEN** task read requests succeed or are rejected by the boundary
- **THEN** no task or run state is created, modified, or deleted; pagination, task type/status filters, and ordering keep their existing behavior; the shared `get_task`/`_get_task_or_raise` scope semantics remain unchanged

### Requirement: Evaluation task write access boundary

The task management endpoints (`POST /api/evaluation/tasks`, `PUT /api/evaluation/tasks/{task_id}`, `DELETE /api/evaluation/tasks/{task_id}`, `POST /api/evaluation/tasks/{task_id}/enable`, `POST /api/evaluation/tasks/{task_id}/disable`) SHALL enforce one shared write access boundary. Creation SHALL require an explicit active workspace — including for explicit system-scope identities — and a minimum editor role in that workspace; a caller without an active workspace SHALL receive the uniform `403 NO_WORKSPACE` response, and a caller below editor (or without a workspace role) SHALL receive `403 FORBIDDEN`, both before the write service is invoked so that no task row is persisted (the model's zero-UUID workspace default SHALL NOT become a silent binding). Mutations of an existing task SHALL first resolve the task through the task read boundary (existence, not soft-deleted, workspace visibility — ownership before role), then require a minimum editor role; a task failing the visibility conditions SHALL produce the same identifier-free 404 as a missing task, and a non-system identity without an active workspace SHALL be denied before any query. The write service SHALL be invoked with the resolved task's real workspace — for system-scope identities this is the target task's workspace, never a `None` equality condition or the zero-UUID fallback; explicit system-scope identities have the workspace role check waived. Denials SHALL be read-only: the write service SHALL NOT be invoked and tasks, runs, and scores SHALL be unchanged. Successful mutations SHALL keep their existing semantics: partial update with merged-config re-validation, soft delete that retains produced runs and scores, enable/disable restricted to online tasks with `400 INVALID_REQUEST` otherwise, enable-time evaluator re-validation, and unchanged response shapes.

#### Scenario: Creation requires an explicit workspace and editor role
- **WHEN** an editor- or admin-role member of workspace W creates a task, while a viewer or role-less member of W and an identity without an active workspace — including system scope — issue the same request
- **THEN** the member's task is created bound to W and returned active; the viewer and role-less members receive `403 FORBIDDEN`, the workspace-less identities receive `403 NO_WORKSPACE`, and in every denial no task row is persisted

#### Scenario: Mutation of an invisible task is indistinguishable from missing
- **WHEN** a caller updates, deletes, enables, or disables a task that does not exist, is soft-deleted, or belongs to another workspace
- **THEN** every condition receives the same identifier-free 404 as a missing task, including callers holding editor or admin roles in their own workspace

#### Scenario: Role floor applies after visibility
- **WHEN** a viewer-role or role-less member of the task's workspace calls any mutation endpoint
- **THEN** the response is `403 FORBIDDEN`, the write service is not invoked, and task, run, and score state is unchanged

#### Scenario: Missing-workspace identity is denied before any query
- **WHEN** a validly authenticated non-system identity without an active workspace calls any mutation endpoint
- **THEN** the response is the identifier-free 404 and no database query is issued

#### Scenario: System scope mutates through the target task's real workspace
- **WHEN** an explicit system-scope identity mutates a task bound to a non-default workspace
- **THEN** the mutation completes and lands on that task with its workspace unchanged; the service receives the task's real workspace, not a `None` equality condition or the zero-UUID fallback, and a missing task still yields the identifier-free 404

#### Scenario: Mutation semantics are preserved
- **WHEN** an allowed mutation succeeds, a visible task carries an invalid merged configuration, or an offline task is enabled or disabled
- **THEN** update re-validates the merged configuration and returns `400 INVALID_REQUEST` on violation; delete and disable retain all produced runs and scores; enable/disable reject offline tasks with `400 INVALID_REQUEST`; successful response shapes are unchanged

### Requirement: Evaluation score read access boundary

The score query endpoint (`GET /api/evaluation/scores`) SHALL enforce one shared read access boundary. A request without `task_id` SHALL be a workspace-scoped query: every identity without an active workspace — including explicit system scope — SHALL be denied with 403 `NO_WORKSPACE` before any query, and a non-system identity with an active workspace SHALL hold a minimum viewer role in that workspace; explicit system scope SHALL retain its role exemption when an active workspace is supplied. A request with `task_id` SHALL resolve the task through the task read boundary first: a task that does not exist, is soft-deleted, or is invisible to the caller SHALL produce the task endpoints' constant identifier-free 404 — not an empty successful list — with ownership checked before a minimum viewer role is enforced for non-system identities; a non-system identity without an active workspace SHALL receive the same 404 before any query. Explicit system-scope identities SHALL read across workspaces, and the score query SHALL then be constrained to the task's real workspace. Score queries SHALL apply the task/workspace condition, the not-deleted condition, and every requested filter to both `items` and `total`. The boundary SHALL NOT change successful response shapes, pagination, `created_at` ascending ordering, the existing filter parameters (`target_id`, `session_id`, `metric_name`, `status`, `source`), the ability to query manually annotated rows (`task_id` NULL rows returned when `task_id` is omitted, `source=human` supported), or the ability to read scores of a disabled task, and the endpoint SHALL remain read-only.

#### Scenario: Query without task_id is scoped to the caller workspace
- **WHEN** score rows exist under tasks in workspaces W1 and W2 and a W1 viewer calls `GET /api/evaluation/scores` without `task_id`
- **THEN** only W1 scores are returned and `total` counts only W1 scores

#### Scenario: Workspace-scoped queries require an active workspace
- **WHEN** any identity — including explicit system scope — calls the score endpoint without task_id and without an active workspace
- **THEN** the response SHALL be 403 with the `NO_WORKSPACE` code before any query; the system identity's null workspace SHALL NOT be applied as an equality condition that silently yields an empty list

#### Scenario: Role floor inside the caller workspace
- **WHEN** a non-system identity whose active workspace contains scores but whose workspace membership role is below viewer (or absent) calls the score endpoint
- **THEN** the response SHALL be 403 authorization denied and no score content SHALL be returned

#### Scenario: Scores of an invisible task return not-found, not an empty list
- **WHEN** `task_id` names a task that does not exist, is soft-deleted, or is invisible to the caller
- **THEN** the response SHALL be the task endpoints' constant identifier-free 404 with the same body for all conditions; the API SHALL NOT return an empty successful list in place of the resource visibility denial

#### Scenario: System scope reads a visible task's scores across workspaces
- **WHEN** an explicit system-scope identity queries scores with `task_id` naming a task in any workspace
- **THEN** the request succeeds and the scores SHALL be constrained to that task's real workspace

#### Scenario: Boundary preserves query semantics and read-only guarantee
- **WHEN** score queries succeed or are rejected by the boundary
- **THEN** no score or task state is created, modified, or deleted; pagination, `created_at` ascending ordering, the existing filter parameters, manual-annotation queries (`task_id` NULL rows when `task_id` is omitted, `source=human`), disabled-task score reads, and response shapes remain unchanged

#### Scenario: Missing workspace non-system task query is denied before lookup
- **WHEN** an authenticated non-system identity without an active workspace calls the score endpoint with task_id
- **THEN** the response SHALL be the task endpoints' constant identifier-free 404 before any resource or score query

#### Scenario: Task-scoped role check follows visibility
- **WHEN** a role-less non-system identity queries a visible task in its own workspace or an invisible task in another workspace
- **THEN** the visible task SHALL produce 403 FORBIDDEN and the invisible task SHALL produce the same 404 as a missing task, with no score query in either case
