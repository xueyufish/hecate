## ADDED Requirements — 新增需求

### Requirement: ModelPricingModel ORM model — ModelPricingModel ORM 模型
系统 SHALL 在 `models/model_pricing.py` 中定义 `ModelPricingModel(BaseModel)`，字段：`model_id`（String 255，提供者模型名称，例如 "gpt-4o"）、`display_name`（String 255）、`input_price_per_1k`（Float，每 1K 输入 token 成本，美元）、`output_price_per_1k`（Float，每 1K 输出 token 成本，美元）、`currency`（String 8，默认 "USD"）、`effective_from`（DateTime，此定价生效时间）、`effective_until`（DateTime 可为空，此定价过期时间，NULL 表示当前）、`workspace_id`（UUID，默认零 UUID）。

#### Scenario: Create pricing entry — 场景：创建定价条目
- **WHEN** 使用 `model_id="gpt-4o"`、`input_price_per_1k=0.0025`、`output_price_per_1k=0.01`、`effective_from=2026-01-01` 创建 `ModelPricingModel`
- **THEN** 记录以 `effective_until=None` 和 `currency="USD"` 持久化

#### Scenario: Default currency — 场景：默认货币
- **WHEN** 创建 `ModelPricingModel` 时未指定 `currency`
- **THEN** `currency` 为 `"USD"`

#### Scenario: Effective range overlap prevention — 场景：有效范围重叠预防
- **WHEN** 为 `model_id="gpt-4o"` 创建新的定价条目，其 `effective_from` 与同一工作空间中现有条目的有效范围重叠
- **THEN** 系统 SHALL 将先前条目的 `effective_until` 设置为新条目的 `effective_from`，确保在任何时间点每个模型只有一个活跃定价

### Requirement: ModelPricingCreateSchema — ModelPricingCreateSchema 模式
系统 SHALL 在 `models/model_pricing.py` 中定义 `ModelPricingCreateSchema`，字段：`model_id`（str，1-255 字符）、`display_name`（str，1-255 字符）、`input_price_per_1k`（float，≥ 0）、`output_price_per_1k`（float，≥ 0）、`currency`（str，默认 "USD"）、`effective_from`（datetime）。

#### Scenario: Valid schema — 场景：有效模式
- **WHEN** 验证 `ModelPricingCreateSchema(model_id="gpt-4o", display_name="GPT-4o", input_price_per_1k=0.0025, output_price_per_1k=0.01, effective_from="2026-01-01T00:00:00")`
- **THEN** 模式被接受

#### Scenario: Negative price rejected — 场景：负数价格被拒绝
- **WHEN** 使用 `input_price_per_1k=-0.01` 构造 `ModelPricingCreateSchema`
- **THEN** 验证失败

### Requirement: ModelPricingReadSchema — ModelPricingReadSchema 模式
系统 SHALL 在 `models/model_pricing.py` 中定义 `ModelPricingReadSchema`，带有 `model_config = ConfigDict(from_attributes=True)` 和所有 ModelPricingModel 字段，包括 `id`、`created_at`、`updated_at`。

#### Scenario: Read schema from ORM — 场景：从 ORM 读取模式
- **WHEN** 从 `ModelPricingModel` ORM 实例创建 `ModelPricingReadSchema`
- **THEN** 所有字段包括 `id`、`effective_from`、`effective_until` 都被填充

### Requirement: Model pricing CRUD API — 模型定价 CRUD API
系统 SHALL 在 `/api/model-pricing` 下公开用于模型定价管理的 REST 端点。

#### Scenario: Create pricing — 场景：创建定价
- **WHEN** 使用有效的 `ModelPricingCreateSchema` 调用 `POST /api/model-pricing`
- **THEN** 创建新的定价记录并以状态 201 返回

#### Scenario: List pricing entries — 场景：列出定价条目
- **WHEN** 调用 `GET /api/model-pricing`
- **THEN** 返回定价条目的分页列表，按 `model_id` 排序

#### Scenario: List pricing filtered by model — 场景：按模型过滤列出定价
- **WHEN** 调用 `GET /api/model-pricing?model_id=gpt-4o`
- **THEN** 仅返回 `model_id="gpt-4o"` 的定价条目

#### Scenario: Update pricing — 场景：更新定价
- **WHEN** 使用更新的 `input_price_per_1k` 调用 `PUT /api/model-pricing/{id}`
- **THEN** 更新定价记录并返回

#### Scenario: Delete pricing — 场景：删除定价
- **WHEN** 调用 `DELETE /api/model-pricing/{id}`
- **THEN** 定价记录被软删除，返回状态 204

### Requirement: Cost calculation from token usage — 从 token 使用量计算成本
系统 SHALL 通过将 `TraceModel.usage` 中的 token 计数乘以匹配的 `ModelPricingModel` 费率来计算成本。对于每个跟踪，成本为 `(prompt_tokens / 1000 × input_price_per_1k) + (completion_tokens / 1000 × output_price_per_1k)`，使用有效范围包含跟踪 `start_time` 的定价条目。

#### Scenario: Calculate cost for a single trace — 场景：计算单个跟踪的成本
- **WHEN** 跟踪的 `usage = {"prompt_tokens": 1000, "completion_tokens": 500}`，模型 "gpt-4o" 的 `input_price_per_1k=0.0025`、`output_price_per_1k=0.01`
- **THEN** 成本为 `(1000/1000 × 0.0025) + (500/1000 × 0.01) = 0.0025 + 0.005 = 0.0075` 美元

#### Scenario: Trace with no matching pricing — 场景：没有匹配定价的跟踪
- **WHEN** 跟踪引用了没有定价条目的模型 "unknown-model"
- **THEN** 成本为 `0.0`，token 计为 `unpriced_tokens`

#### Scenario: Historical pricing applied correctly — 场景：历史定价正确应用
- **WHEN** 2026-01-15 的跟踪使用模型 "gpt-4o"，且定价在 2026-02-01 更改
- **THEN** 成本计算使用 2026-01-15 有效的定价，而不是当前定价

### Requirement: Cost summary API — 成本汇总 API
系统 SHALL 公开 `GET /api/costs/summary`，返回给定时间范围内的总成本、总 token（输入 + 输出）和未定价 token，可选的过滤器为 `user_id`、`agent_id`、`session_id` 和 `model`。

#### Scenario: Summary for a time range — 场景：时间范围汇总
- **WHEN** 调用 `GET /api/costs/summary?start_date=2026-06-01&end_date=2026-06-30`
- **THEN** 响应包含 2026 年 6 月的 `total_cost`、`total_input_tokens`、`total_output_tokens`、`unpriced_tokens`

#### Scenario: Summary filtered by agent — 场景：按 Agent 过滤汇总
- **WHEN** 调用 `GET /api/costs/summary?agent_id={uuid}`
- **THEN** 仅包含具有指定 `agent_id` 的跟踪的成本

#### Scenario: Summary with no traces — 场景：无跟踪的汇总
- **WHEN** 为没有跟踪的时间范围调用 `GET /api/costs/summary`
- **THEN** 响应包含 `total_cost=0.0`、`total_input_tokens=0`、`total_output_tokens=0`、`unpriced_tokens=0`

### Requirement: Cost breakdown API — 成本分类 API
系统 SHALL 公开 `GET /api/costs/breakdown`，返回按指定维度（`group_by` 参数：`model`、`agent`、`user`、`session`）聚合的成本，可选的过滤器为时间范围和筛选条件。

#### Scenario: Breakdown by model — 场景：按模型分类
- **WHEN** 调用 `GET /api/costs/breakdown?group_by=model&start_date=2026-06-01&end_date=2026-06-30`
- **THEN** 响应包含一个 `{key, cost, input_tokens, output_tokens, percentage}` 条目列表，每个模型一个，按成本降序排序

#### Scenario: Breakdown by agent — 场景：按 Agent 分类
- **WHEN** 调用 `GET /api/costs/breakdown?group_by=agent`
- **THEN** 响应包含按 `agent_id` 聚合的成本，`key` 为 Agent UUID 字符串

#### Scenario: Breakdown with percentage calculation — 场景：带百分比计算的分类
- **WHEN** 总成本为 $10.00，模型 "gpt-4o" 占 $6.00
- **THEN** "gpt-4o" 条目的 `percentage = 60.0`

### Requirement: Cost timeseries API — 成本时间序列 API
系统 SHALL 公开 `GET /api/costs/timeseries`，返回随时间变化的成本数据点，带有 `granularity` 参数（`hourly`、`daily`、`monthly`）和可选的过滤器。

#### Scenario: Daily timeseries — 场景：每日时间序列
- **WHEN** 调用 `GET /api/costs/timeseries?granularity=daily&start_date=2026-06-01&end_date=2026-06-07`
- **THEN** 响应包含 7 个数据点，每天一个，每个包含 `{timestamp, cost, input_tokens, output_tokens}`

#### Scenario: Timeseries filtered by model — 场景：按模型过滤的时间序列
- **WHEN** 调用 `GET /api/costs/timeseries?granularity=daily&model=gpt-4o`
- **THEN** 每个数据点仅包含 "gpt-4o" 跟踪的成本

#### Scenario: Empty timeseries — 场景：空时间序列
- **WHEN** 为没有跟踪的时间范围调用 `GET /api/costs/timeseries`
- **THEN** 响应包含范围内每个间隔的零成本数据点

### Requirement: Seed pricing data migration — 种子定价数据迁移
系统 SHALL 包含一个 Alembic 数据迁移，预先填充常见模型（gpt-4o、gpt-4o-mini、gpt-4-turbo、claude-3.5-sonnet、claude-3.5-haiku、deepseek-chat、deepseek-reasoner、gemini-2.0-flash）的 `ModelPricingModel`。

#### Scenario: Migration populates pricing — 场景：迁移填充定价
- **WHEN** 应用迁移
- **THEN** `model_pricings` 表中至少存在 8 个定价条目，`effective_from` 设置为迁移运行日期

#### Scenario: Migration is idempotent — 场景：迁移是幂等的
- **WHEN** 将迁移应用到已具有定价条目的数据库
- **THEN** 不会创建重复条目
