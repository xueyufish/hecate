## Context — 背景

Hecate 已经拥有坚实的模型管理基础：

- **ModelProviderModel** — 提供者注册表，包含加密 API 密钥、base_url、config、status
- **ModelRegistryModel** — 已注册模型，链接到提供者，包含能力（JSON）、max_context、model_type
- **ModelPricingModel** — 带时间范围的定价，具有工作区隔离
- **LLMService** — LiteLLM 包装器，支持流式、工具调用、回退模型
- **ModelRouter** — 4 种路由策略（COST、LATENCY、CAPABILITY、BALANCED）带约束
- **CircuitBreakerManager** — 每个前缀的断路器（CLOSED → OPEN → HALF_OPEN）
- **CostService** — 定价 CRUD，来自 TraceModel.usage 的成本聚合

缺少的是：统一的目录视图、生命周期管理（暂存通道、提升）和智能缓存。Model Hub 在现有基础设施之上增加了这三项能力。

## Goals / Non-Goals — 目标 / 非目标

**目标：**
- 构建可浏览的模型目录 API，聚合注册表和定价数据，支持搜索、过滤和比较
- 为模型添加暂存通道（dev/staging/prod），带提升工作流和审计跟踪
- 为智能路由器添加语义缓存，用于成本和延迟优化
- 添加弃用调度，带自动日落通知
- 与现有的 BudgetService 集成，实现成本感知路由

**非目标：**
- 模型微调流水线（6.6 — 独立功能，大工作量）
- 自托管推理 / 托管模型部署（6.5 — 独立功能，基础设施密集型）
- 模型管理控制台 UI（O10+G4 — 前端，独立变更）
- 多模态模型分类（6.11 — 独立功能）
- A/B 测试框架用于模型比较（未来增强）
- 模型性能基准测试套件（未来增强）

## Decisions — 决策

### Decision 1: 目录作为只读聚合层，而非新表

**选择**：Model Catalog 是 ModelRegistryModel + ModelPricingModel + ModelProviderModel 的服务层聚合，而非单独的数据库表。

**理由**：数据已存在于三个表中。目录表会重复数据并导致同步问题。相反，CatalogService 连接三个表并通过计算字段（有效定价、能力徽章、提供者状态）进行丰富。

**备选方案**：
- 单独的 CatalogModel 表 — 数据重复，同步复杂
- 物化视图 — 特定于 PostgreSQL，不适用于 SQLite/MySQL

### Decision 2: 通过 ModelDeploymentModel 实现暂存通道，而非 ModelRegistryModel 列

**选择**：创建一个单独的 `ModelDeploymentModel` 表，用于跟踪每个模型的暂存通道分配（dev/staging/prod），而不是向 ModelRegistryModel 添加 `channel` 列。

**理由**：一个模型可以同时存在于多个通道中（例如，gpt-4o 用于 Agent 的 prod，gpt-4o-mini 用于测试的 dev）。单独的部署表允许模型和通道之间的多对多关系，带审计跟踪（谁提升的、何时、审批状态）。

**备选方案**：
- ModelRegistryModel 上的单个 `channel` 列 — 限制一个模型只能有一个通道
- 嵌入通道的 ModelVersionModel — 将版本控制与部署混为一谈

### Decision 3: 通过基于哈希的 CacheStrategy ABC 实现语义缓存

**选择**：在 `model_hub/cache.py` 中定义 `CacheStrategyABC`，包含 `get(key)`、`set(key, value, ttl)`、`invalidate(pattern)` 抽象方法。实现 `InMemoryCacheStrategy`（带 TTL 的字典）和 `RedisCacheStrategy`（可选，需要 redis）。

**理由**：遵循现有的 ABC 模式（AuthProviderABC、SecretProviderABC）。缓存键是 (model + messages + temperature) 的 SHA-256 哈希。语义相似性是未来增强——初始实现使用精确哈希匹配。

**备选方案**：
- GPTCache / 语义相似性缓存 — 添加沉重依赖，对于初始版本为时过早
- LiteLLM 内置缓存 — 配置有限，无自定义失效

### Decision 4: 带审批关卡的工作流提升

**选择**：模型提升（dev → staging → prod）需要工作区管理员审批。`ModelDeploymentModel` 跟踪 `approval_status`（pending/approved/rejected）和 `approved_by`。

**理由**：企业客户需要受控的模型发布。审批关卡防止意外的生产模型变更。审计跟踪（谁批准的、何时）满足合规要求。

**备选方案**：
- 自动提升（无需审批）— 对生产环境有风险
- 外部审批系统（Jenkins、ArgoCD）— 基础设施复杂性

### Decision 5: 带日落日期的弃用调度

**选择**：向 ModelDeploymentModel 添加 `deprecated_at` 和 `sunset_at` 字段。当 `sunset_at` 过后，模型自动禁用。

**理由**：给操作员一个宽限期来将 Agent 迁移到新模型。日落日期触发 AlertService 通知，间隔为 30/7/1 天。

**备选方案**：
- 立即弃用（无宽限期）— 破坏正在运行的 Agent
- 单独的 DeprecationScheduleModel — 对于一个简单的日期字段来说过度工程

### Decision 6: 通过 BudgetService 集成实现成本感知路由

**选择**：扩展 ModelRouter，在选择模型前可选地咨询 BudgetService。如果剩余预算低，则路由到更便宜的模型。

**理由**：BudgetService 已经跟踪配额消耗。路由器可以检查 `budget_remaining` 并在预算受限时切换到成本优化策略。

**备选方案**：
- 单独的预算路由器 — 重复路由逻辑
- 无预算集成 — 错过成本优化机会

## Risks / Trade-offs — 风险 / 权衡

- **[缓存失效复杂性]** → 使用 model_id + version 作为缓存键前缀进行针对性失效；TTL 作为安全网
- **[目录查询性能]** → 添加 model_id + provider_id 的数据库索引；考虑分页默认值（每页 50 条）
- **[提升瓶颈]** → 允许工作区管理员在单工作区模式中自我审批；多工作区需要组织管理员
- **[Redis 依赖]** → InMemoryCache 作为默认；RedisCache 仅当配置时；Redis 不可用时不崩溃
- **[模型弃用破坏 Agent]** → 通过 AlertService 在 30/7/1 天前发送日落通知；Agent 在弃用时回退到默认模型

## Migration Plan — 迁移计划

1. **阶段 1：目录** — CatalogService 只读聚合。无模式变更。新增 `/api/models/catalog` 端点。
2. **阶段 2：生命周期** — ModelDeploymentModel 表 + 迁移。ModelRegistryModel 不变。新增部署/提升/弃用端点。
3. **阶段 3：缓存** — CacheStrategyABC + InMemoryCache。通过 ModelRouter 集成到 LLMService。无模式变更。
4. **阶段 4：成本感知路由** — BudgetService 集成到 ModelRouter。无模式变更。

**回滚**：每个阶段独立。目录端点可卸载。部署表可删除。缓存可通过配置标志禁用。

## Open Questions — 开放问题

- 目录是否应支持**模型推荐**（为任务建议最佳模型）？初始：否，未来增强。
- 缓存是否应支持**流式响应**？初始：否，仅缓存非流式完成。流式缓存复杂（部分响应）。
- 提升工作流是否应支持**金丝雀部署**（基于百分比的流量拆分）？初始：否，未来通过 ModelRouter 流量拆分增强。
