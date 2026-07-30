## Context — 背景

该平台已经在 `TraceModel.usage` 中记录了每次 LLM 调用的 token 使用情况（JSON：`{prompt_tokens, completion_tokens, total_tokens}`）。`MetricsStore` 跟踪实时的 `tokens.input` / `tokens.output` 计数器。缺少的是一个将 token 转换为货币成本的价格层，以及回答"我们花了多少钱，按 X 分类？"的聚合 API。

LangFuse 使用一个单独的 `prices` 表，具有每使用类型定价，并在摄取时计算成本。LiteLLM 使用配置文件定价（`input_cost_per_token`）。这两种方法都不能正确处理历史数据的价格变化。

## Goals / Non-Goals — 目标 / 非目标

**目标：**
- 基于数据库的模型定价管理，具有时间范围有效性（价格变化，历史成本必须保持准确）
- 基于现有 TraceModel token 数据 × ModelPricingModel 费率的成本计算
- 多维聚合：按用户、Agent、会话、模型、时间范围
- 用于成本汇总、分类和时间序列的 REST API 端点

**非目标：**
- 实时成本告警（由未来的 8.6 通知功能涵盖）
- 预算执行/请求阻断（LiteLLM 代理处理此问题）
- UI 仪表板渲染（仅 API；前端消费端点）
- 提示缓存成本调整（未来增强）

## Decisions — 决策

### D1: 具有时间范围定价的独立 ModelPricingModel

**选择**：新的 `ModelPricingModel` 表，包含 `effective_from` / `effective_until` 日期时间字段。

**理由**：模型价格经常变化（OpenAI 调整定价，新模型发布）。时间范围定价确保历史成本计算即使在价格更新后仍然准确。这与 LangFuse 的方法匹配，但使用显式时间有效性而不是"新价格仅适用于新跟踪"。

**考虑的替代方案**：
- *扩展 ModelRegistryModel 以包含价格列*：更简单，但无法处理价格变化——更新后历史成本将不正确。
- *配置文件定价（LiteLLM 风格）*：无需数据库管理，但无法通过 API 查询且运行时更新更困难。

### D2: 查询时成本计算

**选择**：通过在模型名称 + 时间范围上将 TraceModel 与 ModelPricingModel 进行 JOIN，在查询时计算成本。

**理由**：由于定价是有时间范围的，在 TraceModel 中存储预先计算的成本需要价格变化时回填。查询时计算始终准确，并且对于典型工作负载，跟踪表不会过大。

**考虑的替代方案**：
- *写入时计算（LangFuse）*：查询更快，但需要价格变化时回填，并且失去历史准确性。
- *混合（写入时 + 定期重新计算）*：对于当前规模来说过度设计。

### D3: 模型名称作为连接键

**选择**：使用从跟踪元数据/使用情况中提取的模型名称将 TraceModel 连接到 ModelPricingModel。

**理由**：TraceModel 没有专用的 `model` 列——模型名称存储在跟踪 `name` 或 `metadata` 中。成本服务将在查询中接受显式的 `model` 参数，该参数在服务层从跟踪元数据中提取。ModelPricingModel 使用 `model_id`（字符串，例如 "gpt-4o"）作为工作空间内的自然键。

### D4: 通过 Alembic 数据迁移的种子定价

**选择**：在迁移中预先填充常见模型（gpt-4o、gpt-4o-mini、claude-3.5-sonnet、claude-3.5-haiku、deepseek-chat 等）的 ModelPricingModel。

**理由**：开箱即用的成本跟踪，无需手动设置定价。用户可以通过 API 覆盖或添加自定义模型。

## Risks / Trade-offs — 风险 / 权衡

- **大型跟踪表上的查询性能** → 缓解：在 TraceModel(session_id, agent_id, start_time) 上添加索引。对于非常大的部署，未来增强可以添加预聚合的成本快照表。
- **跟踪和定价之间的模型名称不匹配** → 缓解：成本服务在定价成本旁边返回 `unpriced_tokens` 计数，以便管理员可以识别未配置定价的模型。
- **定价数据过时** → 缓解：种子迁移涵盖常见模型；API 允许运行时更新。未来增强：从 LiteLLM 的模型成本映射同步。
