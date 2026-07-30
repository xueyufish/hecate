## ADDED Requirements — 新增的需求

### Requirement: Dataset management API — 需求：数据集管理 API
系统应在 `/api/evaluation/datasets` 暴露用于数据集 CRUD 的 REST 端点，以及在 `/api/evaluation/datasets/{dataset_id}/items` 暴露用于项管理的端点。

#### Scenario: Create dataset via API — 场景：通过 API 创建数据集
- **WHEN — 当** 发送 POST 请求到 `/api/evaluation/datasets`，包含 `{"name": "test-set", "description": "..."}`
- **THEN — 则** API 应返回 201 及创建的数据集，包含生成的 `id`、`created_at`、`updated_at`

#### Scenario: Add items to dataset — 场景：向数据集添加项
- **WHEN — 当** 发送 POST 请求到 `/api/evaluation/datasets/{id}/items`，包含项列表
- **THEN — 则** API 应验证每个项，持久化有效项，并返回 201 及添加的项数量

#### Scenario: List datasets with pagination — 场景：带分页列出数据集
- **WHEN — 当** 发送 GET 请求到 `/api/evaluation/datasets?page=1&page_size=20`
- **THEN — 则** API 应返回带总数的分页数据集列表

#### Scenario: Delete dataset — 场景：删除数据集
- **WHEN — 当** 发送 DELETE 请求到 `/api/evaluation/datasets/{id}`
- **THEN — 则** API 应级联删除数据集及其所有项，返回 204

### Requirement: Evaluation run API — 需求：评估运行 API
系统应在 `/api/evaluation/runs` 暴露用于创建和检索评估运行的 REST 端点。

#### Scenario: Create evaluation run — 场景：创建评估运行
- **WHEN — 当** 发送 POST 请求到 `/api/evaluation/runs`，包含 `{"dataset_id": "...", "evaluators": ["faithfulness", "context_precision"]}`
- **THEN — 则** API 应对数据集项执行指定的评估器，持久化运行及所有分数，并返回 201 及 `EvaluationRunResult`

#### Scenario: List evaluation runs — 场景：列出评估运行
- **WHEN — 当** 发送 GET 请求到 `/api/evaluation/runs?dataset_id=...`
- **THEN — 则** API 应按 dataset_id 过滤返回运行，带摘要统计

#### Scenario: Get run scores — 场景：获取运行分数
- **WHEN — 当** 发送 GET 请求到 `/api/evaluation/runs/{id}/scores`
- **THEN — 则** API 应返回运行的所有单个分数，按评估器指标分组

### Requirement: Authentication required — 需求：需要认证
所有评估 API 端点应通过现有的 JWT/API 密钥中间件要求认证。

#### Scenario: Unauthenticated request — 场景：未认证的请求
- **WHEN — 当** 未提供有效认证凭据的请求
- **THEN — 则** API 应返回 401 Unauthorized
