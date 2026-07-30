## Why — 为什么

Hecate 现有的模型管理是以提供者为中心的：ModelProviderModel 存储连接凭据，ModelRegistryModel 列出可用模型，但没有统一的目录供用户浏览、比较和按能力选择模型。Model Lifecycle Manager (6.45) 添加了暂存通道（dev/staging/prod）和提升工作流，使操作员能够安全地推出模型变更。Intelligent Router (6.14) 添加了请求级路由和缓存，以优化成本和延迟。这三项功能共同将 Hecate 从"模型连接管理器"转变为"模型中心"——操作员管理整个模型集群的核心位置。

## What Changes — 变更内容

- **Model Catalog (6.44)**：构建一个可浏览、可搜索的模型目录 API，聚合 ModelRegistryModel 条目和 ModelPricingModel 的定价数据。添加能力徽章（vision、function-calling、streaming、context-length）、提供者比较矩阵和一键启用工作流。目录通过元数据标签、类别分组和搜索/过滤能力丰富现有的模型数据。
- **Model Lifecycle Manager (6.45)**：向 ModelRegistryModel 添加暂存通道（dev/staging/prod）。实现带审批关卡的提升工作流——模型从 dev 到 staging 再到 prod，带审计跟踪。添加带自动日落通知的弃用调度和回滚支持。引入 ModelVersionModel 以跟踪版本化的模型配置。
- **Intelligent Router with Caching (6.14)**：在现有的 RoutingStrategy 基础上构建，添加语义缓存（相似提示返回缓存响应）、成本感知路由（预算低时路由到更便宜的模型）和延迟感知回退。添加 CacheStrategy ABC，包含 InMemoryCache 和 RedisCache 实现。

## Capabilities — 能力

### 新能力

- `model-catalog`：可浏览/可搜索的模型目录，带能力徽章、提供者比较矩阵、类别分组和搜索/过滤。聚合 ModelRegistryModel + ModelPricingModel 数据为统一目录视图。用于列出、过滤、比较和启用模型的 REST API。
- `model-lifecycle`：版本化的模型注册表，带暂存通道（dev/staging/prod）、带审批关卡的工作流提升、带日落通知的弃用调度和回滚支持。通过通道和版本跟踪扩展现有的 ModelRegistryModel。
- `intelligent-router`：带 TTL 的语义缓存、成本感知路由、延迟感知回退和每个 Agent 的模型覆盖。CacheStrategy ABC，包含 InMemoryCache 和 RedisCache 实现。与现有的 RoutingStrategy 和 BudgetService 集成。

### 修改的能力

（无——所有新能力建立在现有的 ModelProviderModel、ModelRegistryModel、ModelPricingModel、LLMService 和 RoutingStrategy 之上，不改变其规范级行为）

## Impact — 影响

- **新模块**：`src/hecate/model_hub/`（目录服务、生命周期服务、智能路由器）、`src/hecate/models/model_version.py`（ModelVersionModel）、`src/hecate/models/model_deployment.py`（用于暂存通道跟踪的 ModelDeploymentModel）
- **修改的现有文件**：`src/hecate/models/model_provider.py`（向 ModelRegistryModel 添加 `channel` 和 `version` 字段）、`src/hecate/services/llm/routing.py`（集成缓存）、`src/hecate/main.py`（注册模型中心路由器）
- **新 API 端点**：`/api/models/catalog`、`/api/models/{id}/promote`、`/api/models/{id}/deprecate`、`/api/models/deployments`、`/api/router/cache/stats`、`/api/router/strategy`
- **数据库迁移**：ModelVersionModel 表、ModelDeploymentModel 表、ModelRegistryModel 的 channel/version 列
- **新依赖**：`redis`（可选，用于 RedisCache），无其他新包
