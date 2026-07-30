## Context — 背景

Hecate 的 Model Hub 第一阶段（Model Catalog 6.44、Lifecycle Manager 6.45、Intelligent Router 6.14）奠定了基础设施：模型发现、分阶段通道和语义缓存路由。现有的 `CostService` 提供定价 CRUD 和基于 `TraceModel.usage` 的成本计算。`ModelRegistryModel.model_type` 是单个字符串，默认值为 `"chat"`。前端（`web/`）有一个 Next.js 模型管理页面，包含提供商 CRUD、模型列表和调试 playground。

对 12+ 平台（vLLM、OpenAI、Bedrock、Vertex AI、watsonx、Salesforce Agentforce、Palantir AIP、Dify、LiteLLM、NullSpend、ai-finops-radar、OpenLLMetry）的行业研究确认了清晰的模式：Agent 平台不编排推理服务器（只有云基础设施平台才做这件事）；微调是基于提供商 API 的（非本地训练）；LLM 可观测性正在汇聚到 OpenTelemetry GenAI 语义约定；模型分类正从枚举转向结构化元数据（模态 + 能力）。

## Goals / Non-Goals — 目标 / 非目标

**目标：**
- 完成 Model Hub 的 5 个剩余功能（6.4、6.11、6.5、6.6、O10+G4）
- 带可配置执行策略（告警或阻断）的成本治理
- 支持模态感知路由的多模态模型分类
- 外部推理端点管理（注册 + 健康 + 指标）
- 基于提供商 API 且可插拔后端的微调工作流
- 带趋势可视化的监控控制台（全栈：API + 前端）

**非目标：**
- 推理服务器生命周期编排（启动/停止 vLLM、GPU 分配、K8s 扩缩容）——这属于 vLLM production-stack / AIBrix / KServe 的范畴
- 本地微调（GPU 训练）——我们委托给提供商 API（OpenAI、Bedrock）
- 质量回归检测——推迟到 Sprint 7（依赖于 Evaluation Suite）
- 多提供商微调适配器（Bedrock、Vertex）——OpenAI 是参考实现；其他提供商通过相同的 ABC 后续添加

## Decisions — 决策

### D1: 模型元数据模式——结构化模态 JSON（选项 B）

**决策**：将 `model_type: str` 替换为 `model_metadata: JSON`，包含 `{modalities: {input: [], output: []}, capabilities: {}, limits: {}}`。

**理由**：结构化模态（输入/输出分离）自然地表达了多模态模型（GPT-4o：input=[text,image,audio]）和生成模型（DALL-E：output=[image]），无需枚举每种组合。与 basellm/llm-metadata 和 IETF ACPM 草案一致。Dify 的枚举+功能方法需要为每个新的模型类别添加新的枚举值。

**向后兼容性**：`model_type` 列保留；`model_metadata` 是附加的。计算访问器从 `model_metadata.modalities` 派生 `model_type`，用于向后兼容的读取操作。迁移根据当前的 `model_type` 值为现有行填充 `model_metadata`。

**考虑的备选方案**：Dify 风格的枚举（`model_type` + `features[]`）——更简单但只能通过修改枚举来扩展；不区分输入/输出模态。

### D2: 成本预算执行——通过 PreLLMHook 的可配置策略

**决策**：预算使用 `policy: "alert" | "block"` 配置。当为 `block` 时，PreLLMHook 在每次 LLM 调用前检查预算，如果超限则抛出 `BudgetExceededError`。

**理由**：纯告警（事后）存在凌晨 3 点成本失控的风险。全阻断在开发阶段过于激进。可配置策略让每个工作区自行选择。PreLLMHook 是现有的调用前拦截扩展点。模式受 NullSpend 的请求前执行机制启发。

**异常检测**：滚动窗口（30 天窗口，可配置）每日支出的 Z-score。同一算法也用于性能指标（延迟、错误率）的漂移检测（O10+G4）。

**考虑的备选方案**：仅事后告警（超限风险）；速度熔断器（NullSpend 模式——更复杂，已推迟）。

### D3: 推理端点管理——注册 + 健康，非编排

**决策**：`InferenceBackendABC`，包含 `health_check(endpoint)` 和 `invoke(endpoint, request)`。Hecate 存储端点元数据（URL、model_id、backend_type）并定期轮询 `/health`。不启动/停止服务器或管理 GPU。

**理由**：所有被调研的 Agent 平台（Salesforce BYOLLM、Palantir AIP、Dify、Claude Code）都连接外部端点而不管理基础设施。只有云平台（Bedrock、Vertex AI、watsonx）管理推理——因为基础设施本身就是它们的产品。vLLM production-stack 处理编排；Hecate 消费其 OpenAI 兼容的 API。

**考虑的备选方案**：完整的推理编排（vLLM 生命周期管理）——需要 K8s 集成、GPU 调度，并重复 vLLM production-stack 的功能。

### D4: 微调——基于 ABC 的提供商 API 编排

**决策**：`FineTuningBackendABC`，包含 `submit_job`、`poll_status`、`cancel_job`、`get_result`。OpenAI 适配器是参考实现。InMemory 存根用于测试。其他提供商（Bedrock、Vertex）后续通过相同接口添加。

**理由**：不同提供商的微调 API 差异很大（OpenAI：JSONL 上传；Bedrock：S3+IAM+VPC；Vertex：不同的 SDK）。生态系统中不存在通用的抽象（LiteLLM 的微调模块只覆盖 OpenAI+Azure+Anthropic）。ABC 规范化工作流（上传 → 创建 → 轮询 → 部署），同时适配器处理提供商特定细节。

**数据集存储**：`DatasetModel` 存储元数据 + 文件引用（MinIO URL 或上传路径）。实际文件内容存储在 MinIO（已部署）中，非数据库。

**考虑的备选方案**：本地训练（需要 GPU 基础设施）；从第一天起多提供商（Bedrock/Vertex 的复杂性没有即时需求）。

### D5: 监控控制台——使用 Recharts 的全栈

**决策**：后端聚合 API（`/api/monitoring/models/{id}/performance`、`/api/monitoring/cost/trends`）+ 使用 Recharts（通过 shadcn/ui Chart 组件捆绑）的前端 Dashboard 页面。

**理由**：shadcn/ui 已经包含基于 Recharts 的图表组件——零额外依赖，样式一致性。需要的图表（线形趋势图、柱状比较图、环形分解图）是标准化的。ECharts 的高级功能（热力图、网络图）不必要。

**漂移检测**：性能时间序列的 Z-score（与成本异常检测相同的算法，不同的指标输入）。

**质量回归**：推迟到 Sprint 7。在路线图中记录为 TBD，位于 Evaluation Suite 下。

**考虑的备选方案**：ECharts（更重、与 shadcn/ui 不一致）；仅 API 无前端的交付（仓库是全栈的，非仅后端）。

### D6: 模块结构

新代码位于现有的 `src/hecate/model_hub/` 中，与第一阶段模块并列：

```
model_hub/
├── catalog_service.py        (existing — 6.44)
├── lifecycle_service.py      (existing — 6.45)
├── intelligent_router.py     (existing — 6.14)
├── cache.py                  (existing)
├── cost_management.py        (NEW — 6.4: budgets, anomaly, forecasting)
├── inference_manager.py      (NEW — 6.5: endpoint registration, health)
├── fine_tuning.py            (NEW — 6.6: job orchestration, ABC)
└── monitoring.py             (NEW — O10+G4: aggregation queries)
```

新模型在 `src/hecate/models/` 中。新 API 路由在 `src/hecate/api/management/` 中。前端页面在 `web/src/app/(dashboard)/settings/models/` 下。

## Risks / Trade-offs — 风险 / 权衡

- **[model_metadata 迁移]** 现有行只有 `model_type="chat"` → 迁移使用保守默认值 `{modalities: {input: ["text"], output: ["text"]}, capabilities: {}, limits: {}}` 填充 `model_metadata`。用户可手动或通过提供商同步丰富每个模型的元数据。

- **[预算执行延迟]** PreLLMHook 预算检查为每次 LLM 调用增加一次数据库查询 → 查询按 (workspace_id, model_id, period) 建立索引。亚毫秒级开销。可接受。

- **[推理健康检查误报]** 外部端点可能短暂不可达 → 可配置重试（3 次尝试，5 秒间隔），然后标记为不健康。健康状态是信息性的，非阻断性（请求路由到健康端点，但如果一个端点临时不可用，不会失败）。

- **[微调任务长时间运行]** 提供商的微调任务可能需要数小时 → 带有可配置间隔（默认 60 秒）的异步轮询。任务状态持久化在数据库中。任务完成时的 Webhook 通知（可选）。

- **[Recharts 包大小]** 前端增加约 100KB → 仅监控页面延迟加载。对管理控制台可接受。
