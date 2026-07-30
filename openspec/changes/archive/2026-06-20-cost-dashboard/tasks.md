## 1. 模型层 — ModelPricingModel

- [x] 1.1 在 `models/model_pricing.py` 中定义 `ModelPricingModel(BaseModel)`，字段：`model_id`（String 255）、`display_name`（String 255）、`input_price_per_1k`（Float）、`output_price_per_1k`（Float）、`currency`（String 8，默认 "USD"）、`effective_from`（DateTime）、`effective_until`（DateTime 可为空）、`workspace_id`（UUID，默认零 UUID）
- [x] 1.2 添加索引：在 (model_id, workspace_id, deleted) 上的 `idx_model_pricings_model`，在 (effective_from, effective_until) 上的 `idx_model_pricings_effective`
- [x] 1.3 定义 `ModelPricingCreateSchema`，带验证（model_id 1-255 字符，价格 ≥ 0，必需 effective_from）
- [x] 1.4 定义 `ModelPricingUpdateSchema`，所有字段可选
- [x] 1.5 定义 `ModelPricingReadSchema`，带 `ConfigDict(from_attributes=True)` 和所有模型字段，包括 id、created_at、updated_at
- [x] 1.6 编写模型测试：创建所有字段、创建带默认值（currency、workspace_id）、从属性的 ReadSchema

## 2. Alembic 迁移 — 表 + 种子数据

- [x] 2.1 创建 Alembic 迁移以添加 `model_pricings` 表，包含所有列和索引（从当前头 `d4d66ddd6959` 链接）
- [x] 2.2 在迁移中添加种子数据：插入 gpt-4o（$2.50/$10.00 per 1M = $0.0025/$0.01 per 1K）、gpt-4o-mini（$0.15/$0.60）、gpt-4-turbo（$10.00/$30.00）、claude-3.5-sonnet（$3.00/$15.00）、claude-3.5-haiku（$0.25/$1.25）、deepseek-chat（$0.14/$0.28）、deepseek-reasoner（$0.55/$2.19）、gemini-2.0-flash（$0.10/$0.40）的定价条目
- [x] 2.3 将所有种子条目的 `effective_from` 设置为迁移运行日期（使用 `datetime.now(UTC)`），`effective_until` 设置为 NULL
- [x] 2.4 使种子插入幂等——在插入前检查条目是否已存在

## 3. 服务层 — 定价 CRUD + 有效范围重叠

- [x] 3.1 创建 `services/cost_service.py`，包含 `CostService(db: AsyncSession)` 类
- [x] 3.2 实现 `create_pricing(data: ModelPricingCreateSchema, workspace_id) -> ModelPricingReadSchema`——创建时，将先前活跃条目的 `effective_until` 设置为新条目的 `effective_from`（重叠预防）
- [x] 3.3 实现 `list_pricing(workspace_id, model_id: str | None, page, page_size) -> dict`——带可选 model_id 过滤器的分页列表
- [x] 3.4 实现 `update_pricing(pricing_id, data: ModelPricingUpdateSchema) -> ModelPricingReadSchema`
- [x] 3.5 实现 `delete_pricing(pricing_id) -> None`——软删除
- [x] 3.6 实现 `get_effective_pricing(model_id: str, at_time: datetime, workspace_id) -> ModelPricingModel | None`——查找有效范围包含 `at_time` 的定价条目

## 4. 服务层 — 成本计算 + 聚合

- [x] 4.1 实现 `get_cost_summary(start_date, end_date, user_id, agent_id, session_id, model, workspace_id) -> CostSummarySchema`——从 TraceModel JOIN ModelPricingModel 聚合总成本、总 token、未定价 token
- [x] 4.2 实现 `get_cost_breakdown(group_by, start_date, end_date, filters, workspace_id) -> list[CostBreakdownEntrySchema]`——按模型/Agent/用户/会话聚合，带百分比计算
- [x] 4.3 实现 `get_cost_timeseries(granularity, start_date, end_date, filters, workspace_id) -> list[CostTimeseriesPointSchema]`——时间分桶聚合（每小时/每天/每月）
- [x] 4.4 在 `models/model_pricing.py` 中定义响应模式：`CostSummarySchema`、`CostBreakdownEntrySchema`、`CostTimeseriesPointSchema`
- [x] 4.5 处理未定价跟踪——模型不在定价表中的跟踪返回 cost=0，但 token 在 `unpriced_tokens` 中计数

## 5. API 层 — 定价 CRUD 端点

- [x] 5.1 创建 `api/management/model_pricing.py` 路由器
- [x] 5.2 实现 `POST /api/model-pricing`——创建定价条目（201）
- [x] 5.3 实现 `GET /api/model-pricing`——带可选 `model_id` 过滤器和分页的列表
- [x] 5.4 实现 `PUT /api/model-pricing/{id}`——更新定价条目
- [x] 5.5 实现 `DELETE /api/model-pricing/{id}`——软删除（204）
- [x] 5.6 在 `api/management/__init__.py` 或 main app 路由器设置中注册路由器

## 6. API 层 — 成本查询端点

- [x] 6.1 创建 `api/management/costs.py` 路由器
- [x] 6.2 实现 `GET /api/costs/summary`——接受 start_date、end_date、user_id、agent_id、session_id、model 查询参数
- [x] 6.3 实现 `GET /api/costs/breakdown`——接受 group_by（model/agent/user/session）、start_date、end_date 和可选过滤器
- [x] 6.4 实现 `GET /api/costs/timeseries`——接受 granularity（hourly/daily/monthly）、start_date、end_date 和可选过滤器
- [x] 6.5 在 `api/management/__init__.py` 或 main app 路由器设置中注册路由器

## 7. Conftest + 测试

- [x] 7.1 如果需要模型注册，将 `ModelPricingModel` 导入添加到 `tests/conftest.py`
- [x] 7.2 编写模型测试：创建定价、默认货币、从属性的 ReadSchema、重叠预防
- [x] 7.3 编写 CostService 测试：带数据的汇总、空汇总、按模型分类、分类百分比、每日时间序列、未定价 token
- [x] 7.4 编写 API 测试：定价 CRUD（创建/列表/更新/删除）、成本汇总、成本分类、成本时间序列

## 8. 验证

- [x] 8.1 运行 `ruff check src/hecate/ tests/`——零错误
- [x] 8.2 运行 `ruff format --check src/ tests/`——零问题
- [x] 8.3 运行 `mypy src/`——零错误
- [x] 8.4 运行 `python -m pytest tests/ -q`——所有测试通过
