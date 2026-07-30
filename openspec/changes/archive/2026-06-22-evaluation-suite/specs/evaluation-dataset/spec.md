## MODIFIED Requirements — 修改后的需求

### Requirement: Dataset CRUD operations — 需求：数据集 CRUD 操作
The system SHALL provide an `EvaluationDatasetService` in `services/evaluation/dataset_service.py` with async methods: `create_dataset()`, `get_dataset()`, `list_datasets()`, `update_dataset()`, `delete_dataset()`. Datasets SHALL support `version` (String, default "v1.0"), `baseline_run_id` (UUID FK, nullable), `is_locked` (Boolean, default False), and `default_threshold` (Float, nullable) fields.

系统应在 `services/evaluation/dataset_service.py` 中提供 `EvaluationDatasetService`，包含异步方法：`create_dataset()`、`get_dataset()`、`list_datasets()`、`update_dataset()`、`delete_dataset()`。数据集应支持 `version`（String，默认 "v1.0"）、`baseline_run_id`（UUID FK，可空）、`is_locked`（Boolean，默认 False）和 `default_threshold`（Float，可空）字段。

#### Scenario: Create evaluation dataset — 场景：创建评估数据集
- **WHEN** a user creates a dataset with a name and optional description
- **THEN** the system SHALL create an `EvaluationDatasetModel` record with version="v1.0", is_locked=False, and return the dataset with generated UUID and timestamps

- **当**用户创建带有名称和可选描述的数据集
- **则**系统应创建 `EvaluationDatasetModel` 记录，version="v1.0"，is_locked=False，并返回带有生成的 UUID 和时间戳的数据集

#### Scenario: Delete dataset with items — 场景：删除包含项目的数据集
- **WHEN** a user deletes a dataset that contains evaluation items
- **THEN** the system SHALL cascade-delete all associated items and return success

- **当**用户删除包含评估项目的数据集
- **则**系统应级联删除所有关联项目并返回成功

#### Scenario: Lock dataset prevents modifications — 场景：锁定数据集防止修改
- **WHEN** a user attempts to add items to a dataset with `is_locked=True`
- **THEN** the system SHALL reject the operation with 409 Conflict

- **当**用户尝试向 `is_locked=True` 的数据集添加项目
- **则**系统应以 409 Conflict 拒绝操作

#### Scenario: Set baseline run on dataset — 场景：在数据集上设置基线运行
- **WHEN** a user updates a dataset's `baseline_run_id` to a completed run's ID
- **THEN** subsequent regression comparisons SHALL use this run as the baseline

- **当**用户将数据集的 `baseline_run_id` 更新为已完成运行的 ID
- **则**后续回归比较应使用此运行作为基线

### Requirement: Dataset item management — 需求：数据集项目管理
The system SHALL provide methods to add, list, update, and remove items within a dataset. Each item SHALL contain: `query: str`, `expected_answer: str | None`, `context: list[str] | None`, `metadata: dict | None`, `assertions: list[dict] | None`, `tags: list[str] | None`.

系统应提供在数据集中添加、列出、更新和移除项目的方法。每个项目应包含：`query: str`、`expected_answer: str | None`、`context: list[str] | None`、`metadata: dict | None`、`assertions: list[dict] | None`、`tags: list[str] | None`。

#### Scenario: Add items to dataset — 场景：向数据集添加项目
- **WHEN** a user adds a batch of items to a dataset
- **THEN** the system SHALL validate each item has a non-empty `query` field, persist all items, and return the count of added items

- **当**用户向数据集添加一批项目
- **则**系统应验证每个项目有非空的 `query` 字段，持久化所有项目，并返回添加的项目数

#### Scenario: List items with pagination — 场景：带分页列出项目
- **WHEN** a user lists items in a dataset with page and page_size parameters
- **THEN** the system SHALL return items ordered by creation time with total count

- **当**用户使用 page 和 page_size 参数列出数据集中的项目
- **则**系统应按创建时间排序返回项目，包含总数

#### Scenario: Add item with assertions — 场景：添加带断言的項目
- **WHEN** a user creates an item with `assertions=[{"type": "faithfulness", "threshold": 0.85}]`
- **THEN** the item SHALL be persisted with the assertions JSON and used during pass/fail evaluation

- **当**用户创建项目，`assertions=[{"type": "faithfulness", "threshold": 0.85}]`
- **则**项目应以断言 JSON 持久化，并在通过/失败评估中使用

#### Scenario: Add item with tags — 场景：添加带标签的项目
- **WHEN** a user creates an item with `tags=["smoke", "regression"]`
- **THEN** the item SHALL be included in tag-filtered evaluation runs matching either tag

- **当**用户创建项目，`tags=["smoke", "regression"]`
- **则**项目应包含在匹配任一标签的标签过滤评估运行中

### Requirement: Dataset import/export — 需求：数据集导入/导出
The system SHALL support importing datasets from JSON files and exporting datasets to JSON format. The JSON format SHALL include assertions and tags fields for each item.

系统应支持从 JSON 文件导入数据集和将数据集导出为 JSON 格式。JSON 格式应为每个项目包含断言和标签字段。

#### Scenario: Import from JSON with assertions — 场景：从带断言的 JSON 导入
- **WHEN** a user imports a JSON file containing items with `assertions` and `tags` fields
- **THEN** the system SHALL persist assertions and tags alongside query, expected_answer, and context

- **当**用户导入包含 `assertions` 和 `tags` 字段的 JSON 文件
- **则**系统应将断言和标签与 query、expected_answer 和 context 一起持久化

#### Scenario: Export to JSON with assertions — 场景：导出为带断言的 JSON
- **WHEN** a user exports a dataset
- **THEN** the system SHALL produce a JSON file containing all items with their query, expected_answer, context, metadata, assertions, and tags fields

- **当**用户导出数据集
- **则**系统应生成包含所有项目及其 query、expected_answer、context、metadata、assertions 和 tags 字段的 JSON 文件
