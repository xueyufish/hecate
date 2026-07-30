## Why — 为什么

Hecate 的 Model Hub 第一阶段交付了 Model Catalog (6.44)、Lifecycle Manager (6.45) 和 Intelligent Router (6.14)——覆盖了模型发现、分阶段部署和路由。然而，五个关键能力尚未完成：成本治理缺少异常检测和预算执行；模型分类是单一的 `model_type` 字符串，不足以支持多模态路由；自托管推理端点没有注册或健康监控；微调工作流完全缺失；Model Management Console 没有监控仪表板。这些差距阻碍了 Hecate 作为完整模型运营平台的能力。

## What Changes — 变更内容

- **模型成本管理 (6.4 G8)**：添加每个模型和每个工作区的成本预算，带 Z-score 异常检测、可配置的执行策略（通过 PreLLMHook 的 `alert` 或 `block`）、支出预测和费用分摊报告。与现有的 BudgetModel (10.7) 集成。
- **多模态模型分类 (6.11 G6)**：将 `model_type: str` 替换为结构化的 `model_metadata` JSON，包含 `modalities`（输入/输出数组）、`capabilities`（布尔标志：reasoning、tool_call、vision）和 `limits`（上下文、输出）。Router 和 Catalog 变得模态感知。
- **托管模型部署 (6.5 G5)**：注册外部推理端点（vLLM/Ollama/OpenAI-compatible）、周期性 `/health` 轮询、Prometheus 指标收集（TTFT、吞吐量、错误率）和模型到端点的路由。Hecate 不编排推理服务器生命周期——仅管理端点元数据和健康状态。
- **微调流水线 (6.6 G7)**：通过提供商 API 的完整工作流——数据集管理（上传、版本、预览）、FineTuningBackendABC 抽象、OpenAI 参考适配器、异步任务状态跟踪（queued → running → succeeded/failed）、微调后模型注册、一键部署。
- **模型管理控制台 + 监控 (O10+G4)**：全栈交付——后端聚合 API（每个模型的性能、成本趋势、漂移检测）+ 前端 Console/Dashboard 页面（性能比较视图、成本分析图表、使用 Recharts 的趋势可视化）。性能漂移检测重用 6.4 的 Z-score。质量回归检测推迟到 Sprint 7（TBD，记录在路线图中）。

## Capabilities — 能力

### 新能力
- `model-cost-management`：每个模型/工作区的成本预算、Z-score 异常检测、可配置执行（alert/block）、支出预测、费用分摊报告
- `model-metadata-schema`：替代单一 model_type 字符串的结构化模型分类——模态（输入/输出）、能力（reasoning/tool_call/vision）、限制（context/output）
- `inference-endpoint-management`：外部推理端点注册、健康检查轮询、Prometheus 指标收集、模型到端点路由、InferenceBackendABC
- `fine-tuning-pipeline`：DatasetModel 管理、FineTuningBackendABC、OpenAI 适配器、异步任务编排、微调后模型注册和部署
- `model-monitoring-console`：后端性能/成本/错误率聚合 API + 前端 Console/Dashboard 页面，包含趋势图表、性能比较、漂移检测

### 修改的能力
- `cost-dashboard`：扩展了每个模型的成本分解和时间序列趋势端点，由监控控制台消费
- `llm-routing`：扩展了模态感知的模型选择——路由器在应用成本/延迟策略前，按所需的输入/输出模态和能力标志过滤候选模型

## Impact — 影响

- **新后端模块**：`src/hecate/model_hub/cost_management.py`、`model_hub/inference_manager.py`、`model_hub/fine_tuning.py`、`model_hub/monitoring.py`
- **新模型**：`InferenceEndpointModel`、`DatasetModel`、`FineTuningJobModel`、`ModelCostBudgetModel`
- **修改的模型**：`ModelRegistryModel`（添加 `model_metadata` JSON 字段）、`ModelPricingModel`（无模式变更，新查询）
- **新 ABC**：`InferenceBackendABC`（health_check、invoke）、`FineTuningBackendABC`（submit_job、poll_status、cancel_job、get_result）
- **新 API 端点**：`/api/models/cost/budgets`、`/api/models/cost/anomalies`、`/api/models/cost/forecast`、`/api/inference/endpoints`、`/api/fine-tuning/datasets`、`/api/fine-tuning/jobs`、`/api/monitoring/models/{id}/performance`
- **前端**：`web/src/app/(dashboard)/settings/models/` 下的新页面——控制台仪表板、成本分析、监控趋势；Recharts 集成
- **数据库迁移**：4 个新表 + 1 个列新增（model_metadata）
- **新依赖**：`recharts`（前端，已通过 shadcn/ui 捆绑），无新的 Python 包（OpenAI 微调通过现有的 `openai` 或 `litellm`）
