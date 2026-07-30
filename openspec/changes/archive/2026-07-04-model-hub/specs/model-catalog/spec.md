## ADDED Requirements — 新增需求

### Requirement: 模型目录聚合服务 — Model catalog aggregation service
系统应在 `model_hub/catalog_service.py` 中定义 `CatalogService`，聚合 ModelRegistryModel、ModelProviderModel 和 ModelPricingModel 为统一的目录视图，带计算字段。

#### Scenario: 列出目录条目 — List catalog entries
- **WHEN** 调用 `list_models()`，带可选过滤器（provider、capability、model_type、min_context、max_cost）
- **THEN** 服务应返回目录条目列表，每个条目包含 model_id、display_name、provider_name、capabilities、max_context、effective_pricing 和 provider_status

#### Scenario: 获取单个目录条目 — Get single catalog entry
- **WHEN** 调用 `get_model(model_id)`
- **THEN** 服务应返回详细的目录条目，包含所有元数据，包括定价历史、能力徽章和提供者信息

#### Scenario: 按能力搜索模型 — Search models by capability
- **WHEN** 调用 `search_models(capabilities=["vision", "function_calling"])`
- **THEN** 服务应过滤 ModelRegistryModel，其中 `capabilities` JSON 字段包含所有请求的能力

#### Scenario: 比较模型 — Compare models
- **WHEN** 调用 `compare_models(model_ids=["gpt-4o", "claude-3.5-sonnet"])`
- **THEN** 服务应返回比较矩阵，包含每个模型的定价、上下文窗口、能力和提供者信息

### Requirement: 模型目录 REST API — Model catalog REST API
系统应在 `/api/models/catalog` 暴露端点，用于浏览、搜索和比较模型。

#### Scenario: 带分页的目录列表 — List catalog with pagination
- **WHEN** 收到 GET `/api/models/catalog?page=1&page_size=20&provider=openai&capability=vision`
- **THEN** 系统应返回匹配过滤器的分页目录条目，包含总数

#### Scenario: 获取模型详情 — Get model details
- **WHEN** 收到 GET `/api/models/catalog/{model_id}`
- **THEN** 系统应返回完整的目录条目，包含定价历史和提供者详情

#### Scenario: 比较模型 — Compare models
- **WHEN** 收到 GET `/api/models/catalog/compare?model_ids=gpt-4o,claude-3.5-sonnet`
- **THEN** 系统应返回比较矩阵

#### Scenario: 按定价层级过滤 — Filter by pricing tier
- **WHEN** 收到 GET `/api/models/catalog?max_input_price=0.01`
- **THEN** 系统应仅返回有效输入定价低于或等于阈值的模型

### Requirement: 能力徽章 — Capability badges
系统应从 ModelRegistryModel.capabilities JSON 字段计算能力徽章，并在目录中呈现为结构化标签。

#### Scenario: 支持视觉的模型 — Vision-capable model
- **WHEN** 模型在其 capabilities JSON 中有 `{"vision": true}`
- **THEN** 目录条目应包含 `"capability_badges": ["vision"]`

#### Scenario: 多能力模型 — Multi-capability model
- **WHEN** 模型在 capabilities 中有 `{"vision": true, "function_calling": true, "streaming": true}`
- **THEN** 目录条目应包含 `"capability_badges": ["vision", "function_calling", "streaming"]`

### Requirement: 有效定价计算 — Effective pricing computation
系统应通过查询 ModelPricingModel 获取当前活动的定价记录，计算每个目录条目的有效定价。

#### Scenario: 具有活动定价的模型 — Model with active pricing
- **WHEN** 模型具有满足 `effective_from <= now < effective_until`（或 effective_until 为 NULL）的定价条目
- **THEN** 目录条目应包含 `effective_pricing: {input_per_1k, output_per_1k, currency}`

#### Scenario: 无定价的模型 — Model without pricing
- **WHEN** 模型没有匹配的定价条目
- **THEN** 目录条目应包含 `effective_pricing: null` 和 `has_pricing: false`
