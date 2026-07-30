## ADDED Requirements — 新增的需求

### Requirement: Dataset CRUD operations — 需求：数据集 CRUD 操作
系统应在 `services/evaluation/dataset_service.py` 中提供一个 `EvaluationDatasetService`，包含异步方法：`create_dataset()`、`get_dataset()`、`list_datasets()`、`update_dataset()`、`delete_dataset()`。

#### Scenario: Create evaluation dataset — 场景：创建评估数据集
- **WHEN — 当** 用户使用名称和可选的描述创建数据集
- **THEN — 则** 系统应在 PostgreSQL 中创建 `EvaluationDatasetModel` 记录，并返回带生成的 UUID 和时间戳的数据集

#### Scenario: Delete dataset with items — 场景：删除带项的数据集
- **WHEN — 当** 用户删除包含评估项的数据集
- **THEN — 则** 系统应级联删除所有关联的项并返回成功

### Requirement: Dataset item management — 需求：数据集项管理
系统应提供方法以在数据集内添加、列出、更新和移除项。每个项应包含：`query: str`、`expected_answer: str | None`、`context: list[str] | None`、`metadata: dict | None`。

#### Scenario: Add items to dataset — 场景：向数据集添加项
- **WHEN — 当** 用户向数据集添加一批项
- **THEN — 则** 系统应验证每个项具有非空的 `query` 字段，持久化所有项，并返回添加的项数量

#### Scenario: List items with pagination — 场景：带分页列出项
- **WHEN — 当** 用户使用 page 和 page_size 参数列出数据集中的项
- **THEN — 则** 系统应按创建时间返回项，带总数

### Requirement: Dataset import/export — 需求：数据集导入/导出
系统应支持从 JSON 文件导入数据集和将数据集导出为 JSON 格式。

#### Scenario: Import from JSON — 场景：从 JSON 导入
- **WHEN — 当** 用户导入包含 `{query, expected_answer, context}` 对象数组的 JSON 文件
- **THEN — 则** 系统应验证格式、创建项，并返回导入统计（total、valid、skipped）

#### Scenario: Export to JSON — 场景：导出为 JSON
- **WHEN — 当** 用户导出数据集
- **THEN — 则** 系统应生成一个包含所有项及其 query、expected_answer、context 和 metadata 字段的 JSON 文件
