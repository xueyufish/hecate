## Why — 为什么

该平台记录每次 LLM 调用的 token 使用情况（TraceModel.usage），但无法回答"这花了多少钱？"——没有模型定价表，没有成本计算，没有聚合 API。管理员无法按用户、Agent、会话或模型跟踪支出，使得预算管理变得不可能。

## What Changes — 变更内容

- **新的 `ModelPricingModel`** — 基于数据库的模型定价表，具有时间范围定价（effective_from / effective_until），支持每 1K token 的输入/输出费率和自定义模型定义
- **新的 `CostService`** — 从 TraceModel token 使用量 × ModelPricingModel 费率计算成本，具有多维聚合（按用户、Agent、会话、模型、时间范围）
- **新的成本 API 端点** — 汇总（总成本）、分类（分组聚合）、时间序列（每日/每小时趋势）
- **种子定价数据** — 通过迁移预先填充常见模型（gpt-4o、gpt-4o-mini、claude-3.5-sonnet 等）的定价

## Capabilities — 能力

### New Capabilities — 新增能力
- `cost-dashboard`：模型定价管理、从 token 使用量计算成本、以及多维成本聚合 API

### Modified Capabilities — 修改的能力
（无——这是基于现有 TraceModel 数据的纯增量功能）

## Impact — 影响

- **新文件**：`models/model_pricing.py`（ORM + 模式）、`services/cost_service.py`、`api/management/costs.py`、Alembic 迁移
- **现有数据**：TraceModel.usage JSON 已包含 `{prompt_tokens, completion_tokens, total_tokens}`——无需模式更改
- **依赖**：无新的外部包；使用现有的 SQLAlchemy 异步、Pydantic、FastAPI 模式
- **API 表面**：新的 `/api/costs/*` 端点（汇总、分类、时间序列）+ `/api/model-pricing/*` CRUD
