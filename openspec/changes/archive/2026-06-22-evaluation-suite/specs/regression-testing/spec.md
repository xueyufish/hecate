## ADDED Requirements — 新增需求

### Requirement: Dataset versioning — 需求：数据集版本管理
The system SHALL support dataset versioning via three new fields on `EvaluationDatasetModel`: `version` (String, default "v1.0"), `baseline_run_id` (UUID FK to EvaluationRunModel, nullable), and `is_locked` (Boolean, default False). When a dataset is locked, item additions, modifications, and deletions SHALL be rejected.

系统应通过 `EvaluationDatasetModel` 上的三个新字段支持数据集版本管理：`version`（String，默认 "v1.0"）、`baseline_run_id`（UUID FK 到 EvaluationRunModel，可空）和 `is_locked`（Boolean，默认 False）。当数据集被锁定时，应拒绝添加、修改和删除项目。

#### Scenario: Set baseline run for dataset — 场景：为数据集设置基线运行
- **WHEN** a user sets `baseline_run_id` on a dataset after a successful evaluation run
- **THEN** subsequent regression runs SHALL compare their scores against this baseline run

- **当**用户在成功评估运行后在数据集上设置 `baseline_run_id`
- **则**后续回归运行应将其得分与此基线运行进行比较

#### Scenario: Lock a golden dataset — 场景：锁定黄金数据集
- **WHEN** a user locks a dataset with `is_locked=True`
- **THEN** attempts to add, modify, or delete items SHALL return 409 Conflict

- **当**用户锁定数据集，设置 `is_locked=True`
- **则**尝试添加、修改或删除项目应返回 409 Conflict

#### Scenario: Version tag for dataset — 场景：数据集的版本标签
- **WHEN** a dataset is created with `version="v2.0"`
- **THEN** the version tag SHALL be stored and returned in dataset read responses

- **当**创建版本为 `version="v2.0"` 的数据集
- **则**版本标签应被存储并在数据集读取响应中返回

### Requirement: Per-item assertion model — 需求：每项断言模型
The system SHALL support per-item assertions via an `assertions` JSON field on `EvaluationItemModel`. Each assertion SHALL have a `type` (evaluator name or deterministic check), an optional `threshold` (float), and optional `value` (for deterministic checks). Items without assertions SHALL inherit dataset-level defaults.

系统应通过 `EvaluationItemModel` 上的 `assertions` JSON 字段支持每项断言。每个断言应具有 `type`（评估器名称或确定性检查）、可选的 `threshold`（浮点数）和可选的 `value`（用于确定性检查）。没有断言的项目应继承数据集级别的默认值。

#### Scenario: Item with assertion overrides — 场景：带断言覆盖的项目
- **WHEN** an item has `assertions=[{"type": "faithfulness", "threshold": 0.9}]`
- **THEN** the evaluation engine SHALL apply threshold 0.9 for faithfulness on this item, regardless of dataset default

- **当**项目具有 `assertions=[{"type": "faithfulness", "threshold": 0.9}]`
- **则**评估引擎应对该项目应用 faithfulness 的阈值 0.9，无论数据集默认值如何

#### Scenario: Item with deterministic assertion — 场景：带确定性断言的项目
- **WHEN** an item has `assertions=[{"type": "contains", "value": "RAG"}]`
- **THEN** the engine SHALL check if `generated_answer` contains "RAG" and mark pass/fail without calling an evaluator

- **当**项目具有 `assertions=[{"type": "contains", "value": "RAG"}]`
- **则**引擎应检查 `generated_answer` 是否包含 "RAG" 并标记通过/失败，无需调用评估器

#### Scenario: Item without assertions inherits dataset default — 场景：无断言的项目继承数据集默认值
- **WHEN** an item has no assertions and the dataset has `default_threshold=0.7`
- **THEN** all evaluator scores for this item SHALL use 0.7 as the pass threshold

- **当**项目没有断言且数据集具有 `default_threshold=0.7`
- **则**该项目的所有评估器得分应使用 0.7 作为通过阈值

### Requirement: Dataset default threshold — 需求：数据集默认阈值
The system SHALL support a `default_threshold` Float field on `EvaluationDatasetModel` (nullable, default None). When set, all items without explicit assertions SHALL use this threshold for pass/fail evaluation.

系统应在 `EvaluationDatasetModel` 上支持 `default_threshold` 浮点数字段（可空，默认 None）。设置后，所有没有显式断言的项目应使用此阈值进行通过/失败评估。

#### Scenario: Dataset with default threshold — 场景：带默认阈值的数据集
- **WHEN** a dataset has `default_threshold=0.75` and items without explicit assertions
- **THEN** each evaluator score >= 0.75 on those items SHALL be marked as passed

- **当**数据集具有 `default_threshold=0.75` 且项目没有显式断言
- **则**这些项目上每个评估器得分 >= 0.75 应标记为通过

### Requirement: Item tags for grouping — 需求：用于分组的项目标签
The system SHALL support a `tags` JSON field (array of strings) on `EvaluationItemModel` for categorizing test cases. Tags enable filtered evaluation runs (e.g., only run "smoke" tests, or only "regression" tests).

系统应在 `EvaluationItemModel` 上支持 `tags` JSON 字段（字符串数组），用于分类测试用例。标签支持过滤评估运行（例如，仅运行 "smoke" 测试，或仅运行 "regression" 测试）。

#### Scenario: Filter evaluation run by tags — 场景：按标签过滤评估运行
- **WHEN** an evaluation run is created with `tags=["smoke"]`
- **THEN** only items with "smoke" in their tags array SHALL be evaluated

- **当**创建评估运行时设置 `tags=["smoke"]`
- **则**仅评估标签数组中包含 "smoke" 的项目

#### Scenario: Item with multiple tags — 场景：带多个标签的项目
- **WHEN** an item has `tags=["smoke", "regression", "edge_case"]`
- **THEN** it SHALL be included in runs filtered by any of those tags

- **当**项目具有 `tags=["smoke", "regression", "edge_case"]`
- **则**它应包含在按这些标签中的任何一个过滤的运行中

### Requirement: Run comparison API — 需求：运行比较 API
The system SHALL expose `POST /api/evaluation/runs/compare` that accepts `baseline_run_id` and `candidate_run_id`, computes per-metric score deltas, and flags regressions where score drops exceed a configurable threshold (default 5%).

系统应公开 `POST /api/evaluation/runs/compare`，接受 `baseline_run_id` 和 `candidate_run_id`，计算每个指标的得分差值，并标记得分下降超过可配置阈值（默认 5%）的回归。

#### Scenario: Compare two runs with regression — 场景：比较两次运行（有回归）
- **WHEN** a comparison is made between baseline (faithfulness avg=0.85) and candidate (faithfulness avg=0.72)
- **THEN** the response SHALL include `{"metric": "faithfulness", "baseline_avg": 0.85, "candidate_avg": 0.72, "delta": -0.13, "is_regression": true}`

- **当**在基线（faithfulness 平均值=0.85）和候选（faithfulness 平均值=0.72）之间进行比较
- **则**响应应包含 `{"metric": "faithfulness", "baseline_avg": 0.85, "candidate_avg": 0.72, "delta": -0.13, "is_regression": true}`

#### Scenario: Compare two runs without regression — 场景：比较两次运行（无回归）
- **WHEN** a comparison is made and all metric deltas are within ±5%
- **THEN** the response SHALL include `"overall_regressed": false` and no items in the regressions array

- **当**进行比较且所有指标差值在 ±5% 范围内
- **则**响应应包含 `"overall_regressed": false` 且回归数组中无项目

### Requirement: Per-item pass/fail computation — 需求：每项通过/失败计算
The system SHALL compute per-item pass/fail results during evaluation runs. An item passes if ALL of its assertions (or dataset default threshold) are met. The pass/fail result SHALL be persisted in `EvaluationScoreModel` or a derived computation from scores + assertions.

系统应在评估运行期间计算每个项目的通过/失败结果。如果满足项目所有断言（或数据集默认阈值），则项目通过。通过/失败结果应持久化在 `EvaluationScoreModel` 中，或通过得分 + 断言的衍生计算得出。

#### Scenario: Item passes all assertions — 场景：项目通过所有断言
- **WHEN** an item has assertions for faithfulness (threshold 0.8) and correctness (threshold 0.7), and both scores meet thresholds
- **THEN** the item SHALL be marked as passed

- **当**项目具有 faithfulness（阈值 0.8）和 correctness（阈值 0.7）的断言，且两个得分均满足阈值
- **则**项目应标记为通过

#### Scenario: Item fails one assertion — 场景：项目失败一个断言
- **WHEN** an item has assertions for faithfulness (threshold 0.8, actual 0.6) and correctness (threshold 0.7, actual 0.9)
- **THEN** the item SHALL be marked as failed with the failing assertion identified

- **当**项目具有 faithfulness（阈值 0.8，实际 0.6）和 correctness（阈值 0.7，实际 0.9）的断言
- **则**项目应标记为失败，并标识失败的断言

### Requirement: Regression trigger API for CI/CD — 需求：用于 CI/CD 的回归触发 API
The system SHALL expose `POST /api/evaluation/regression/run` that accepts `dataset_id`, `evaluators` list, optional `tags` filter, optional `threshold` override, and optional `baseline_run_id`. It SHALL execute the evaluation, compare against baseline, and return a structured pass/fail report in a single response.

系统应公开 `POST /api/evaluation/regression/run`，接受 `dataset_id`、`evaluators` 列表、可选的 `tags` 过滤器、可选的 `threshold` 覆盖和可选的 `baseline_run_id`。它应执行评估，与基线比较，并在单个响应中返回结构化的通过/失败报告。

#### Scenario: Successful regression run — 场景：成功的回归运行
- **WHEN** a CI/CD pipeline calls the regression endpoint with a dataset and evaluator list
- **THEN** the response SHALL include `passed: bool`, `total_items`, `passed_items`, `failed_items`, `regressions` array, and `metric_averages`

- **当** CI/CD 管道使用数据集和评估器列表调用回归端点
- **则**响应应包含 `passed: bool`、`total_items`、`passed_items`、`failed_items`、`regressions` 数组和 `metric_averages`

#### Scenario: Regression run with tag filter — 场景：带标签过滤的回归运行
- **WHEN** the regression endpoint is called with `tags=["smoke"]`
- **THEN** only items tagged "smoke" SHALL be evaluated, reducing execution time for CI fast-feedback

- **当**使用 `tags=["smoke"]` 调用回归端点
- **则**仅评估标记为 "smoke" 的项目，减少 CI 快速反馈的执行时间

#### Scenario: Regression detected blocks CI — 场景：检测到回归阻止 CI
- **WHEN** the regression run detects score regressions exceeding the threshold
- **THEN** `passed` SHALL be `false` and the response SHALL include details of each regression for CI to fail the build

- **当**回归运行检测到得分回归超过阈值
- **则** `passed` 应为 `false`，且响应应包含每个回归的详细信息，以便 CI 使构建失败

### Requirement: Run summary with pass/fail statistics — 需求：带通过/失败统计的运行摘要
The system SHALL include pass/fail statistics in the `GET /api/evaluation/runs/{run_id}` response: `total_items`, `passed_items`, `failed_items`, and `pass_rate`.

系统应在 `GET /api/evaluation/runs/{run_id}` 响应中包含通过/失败统计信息：`total_items`、`passed_items`、`failed_items` 和 `pass_rate`。

#### Scenario: Get run with pass/fail summary — 场景：获取运行及通过/失败摘要
- **WHEN** `GET /api/evaluation/runs/{run_id}` is called for a completed run
- **THEN** the response SHALL include `pass_rate` computed as `passed_items / total_items`

- **当**为已完成的运行调用 `GET /api/evaluation/runs/{run_id}`
- **则**响应应包含 `pass_rate`，计算方式为 `passed_items / total_items`
