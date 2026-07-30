## ADDED Requirements — 新增需求

### Requirement: Evaluator listing API — 需求：评估器列表 API
The system SHALL expose `GET /api/evaluation/evaluators` that returns all registered evaluators with their name, description, category, source type (deterministic/llm_judge), and required input fields. Supports optional `category` query parameter for filtering.

系统应公开 `GET /api/evaluation/evaluators`，返回所有已注册的评估器及其名称、描述、分类、来源类型（deterministic/llm_judge）和所需的输入字段。支持可选的 `category` 查询参数进行过滤。

#### Scenario: List all evaluators — 场景：列出所有评估器
- **WHEN** `GET /api/evaluation/evaluators` is called
- **THEN** all 41 registered evaluators are returned with name, description, category, and source_type

- **当**调用 `GET /api/evaluation/evaluators`
- **则**返回所有 41 个已注册评估器及其名称、描述、分类和 source_type

#### Scenario: List evaluators by category — 场景：按分类列出评估器
- **WHEN** `GET /api/evaluation/evaluators?category=process` is called
- **THEN** only evaluators in the "process" category are returned

- **当**调用 `GET /api/evaluation/evaluators?category=process`
- **则**仅返回"process"分类中的评估器

### Requirement: Run comparison API — 需求：运行比较 API
The system SHALL expose `POST /api/evaluation/runs/compare` that accepts `baseline_run_id` and `candidate_run_id`, returns per-metric deltas, per-item pass/fail changes, and regression flags.

系统应公开 `POST /api/evaluation/runs/compare`，接受 `baseline_run_id` 和 `candidate_run_id`，返回每个指标的差值、每个项目的通过/失败变化和回归标志。

#### Scenario: Compare two runs — 场景：比较两次运行
- **WHEN** `POST /api/evaluation/runs/compare` is called with valid baseline and candidate run IDs
- **THEN** the response SHALL include per-metric averages for both runs, deltas, and regression flags for metrics where the candidate score dropped more than the threshold (default 5%)

- **当**使用有效的基线和候选运行 ID 调用 `POST /api/evaluation/runs/compare`
- **则**响应应包含两次运行的每个指标平均值、差值和回归标志（指标候选得分下降超过阈值，默认 5%）

### Requirement: Regression trigger API — 需求：回归触发 API
The system SHALL expose `POST /api/evaluation/regression/run` that accepts `dataset_id`, `evaluators`, optional `tags`, optional `threshold`, and optional `baseline_run_id`. It SHALL execute the evaluation run, compare against baseline if provided, compute pass/fail per item, and return a structured regression report.

系统应公开 `POST /api/evaluation/regression/run`，接受 `dataset_id`、`evaluators`、可选的 `tags`、可选的 `threshold` 和可选的 `baseline_run_id`。它应执行评估运行，与基线比较（如果提供），计算每个项目的通过/失败，并返回结构化回归报告。

#### Scenario: Trigger regression run — 场景：触发回归运行
- **WHEN** `POST /api/evaluation/regression/run` is called with a dataset ID and evaluator list
- **THEN** the response SHALL include `run_id`, `passed`, `total_items`, `passed_items`, `failed_items`, `regressions`, and `metric_averages`

- **当**使用数据集 ID 和评估器列表调用 `POST /api/evaluation/regression/run`
- **则**响应应包含 `run_id`、`passed`、`total_items`、`passed_items`、`failed_items`、`regressions` 和 `metric_averages`

## MODIFIED Requirements — 修改后的需求

### Requirement: Evaluation run API — 需求：评估运行 API
The system SHALL expose REST endpoints at `/api/evaluation/runs` for creating and retrieving evaluation runs. Runs SHALL support optional `tags` parameter for tag-filtered evaluation. The run response SHALL include pass/fail statistics when assertions or thresholds are configured.

系统应在 `/api/evaluation/runs` 公开 REST 端点，用于创建和检索评估运行。运行应支持可选的 `tags` 参数进行标签过滤评估。运行响应应在配置了断言或阈值时包含通过/失败统计信息。

#### Scenario: Create evaluation run — 场景：创建评估运行
- **WHEN** a POST request is sent to `/api/evaluation/runs` with `{"dataset_id": "...", "evaluators": ["faithfulness", "context_precision"]}`
- **THEN** the API SHALL execute the specified evaluators against the dataset items, persist the run with all scores, and return 201 with the `EvaluationRunResult`

- **当**向 `/api/evaluation/runs` 发送 POST 请求，包含 `{"dataset_id": "...", "evaluators": ["faithfulness", "context_precision"]}`
- **则** API 应对数据集项执行指定的评估器，持久化运行及所有得分，并返回 201 及 `EvaluationRunResult`

#### Scenario: Create tag-filtered evaluation run — 场景：创建标签过滤的评估运行
- **WHEN** a POST request is sent to `/api/evaluation/runs` with `{"dataset_id": "...", "evaluators": [...], "tags": ["smoke"]}`
- **THEN** only items tagged "smoke" SHALL be evaluated

- **当**向 `/api/evaluation/runs` 发送 POST 请求，包含 `{"dataset_id": "...", "evaluators": [...], "tags": ["smoke"]}`
- **则**仅评估标记为"smoke"的项目

#### Scenario: Get run with pass/fail summary — 场景：获取运行带通过/失败摘要
- **WHEN** a GET request is sent to `/api/evaluation/runs/{id}`
- **THEN** the response SHALL include `total_items`, `passed_items`, `failed_items`, `pass_rate`, and `metric_averages`

- **当**向 `/api/evaluation/runs/{id}` 发送 GET 请求
- **则**响应应包含 `total_items`、`passed_items`、`failed_items`、`pass_rate` 和 `metric_averages`

#### Scenario: Get run scores — 场景：获取运行得分
- **WHEN** a GET request is sent to `/api/evaluation/runs/{id}/scores`
- **THEN** the API SHALL return all individual scores for the run, grouped by evaluator metric

- **当**向 `/api/evaluation/runs/{id}/scores` 发送 GET 请求
- **则** API 应返回运行的所有单个得分，按评估器指标分组
