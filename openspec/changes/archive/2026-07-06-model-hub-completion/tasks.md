## 1. 模型元数据模式 (6.11)

- [x] 1.1 在 `src/hecate/models/model_provider.py` 的 `ModelRegistryModel` 上添加 `model_metadata: Mapped[dict]` JSON 列
- [x] 1.2 创建 Alembic 迁移来添加 `model_metadata` 列，并为现有行做回填（chat → `{modalities: {input: ["text"], output: ["text"]}, capabilities: {}, limits: {}}`）
- [x] 1.3 添加 `ModelMetadataSchema` Pydantic 模型，包含 `modalities`、`capabilities`、`limits` 字段和验证
- [x] 1.4 添加向后兼容的 `model_type` 计算属性，从 `model_metadata` 派生类型
- [x] 1.5 更新 `CatalogService`，支持按能力徽章（vision、tool_call、reasoning、context size）过滤和显示模型
- [x] 1.6 更新前端模型列表组件（`web/src/app/(dashboard)/settings/models/page.tsx`），从 `model_metadata` 渲染能力徽章
- [x] 1.7 编写测试：元数据模式验证、向后兼容访问器、迁移回填、目录徽章显示

## 2. 模态感知路由（llm-routing 修改）

- [x] 2.1 在 `ModelRouter` 中添加模态过滤——在应用成本/延迟策略前，按所需输入模态过滤候选模型
- [x] 2.2 添加能力过滤——当请求包含工具定义时，优先选择 `tool_call: true` 的模型
- [x] 2.3 添加 `NoCapableModelError` 异常，用于当没有候选模型支持所需模态时
- [x] 2.4 编写测试：图像输入路由到视觉模型、工具请求过滤非工具模型、无合适模型时抛出错误

## 3. 成本预算管理 (6.4)

- [x] 3.1 在 `src/hecate/models/` 中创建 `ModelCostBudgetModel`，字段包括：scope（workspace/agent/user）、target_id、limit_amount、period、currency、policy（alert/block）、workspace_id
- [x] 3.2 为 `model_cost_budgets` 表创建 Alembic 迁移
- [x] 3.3 在 `src/hecate/model_hub/cost_management.py` 中创建 `CostBudgetService`——预算 CRUD、当前支出计算、执行检查
- [x] 3.4 实现分层预算解析——Agent 预算覆盖工作区预算；用户预算覆盖 Agent 预算
- [x] 3.5 实现 `BudgetEnforcementHook` 作为 `PreLLMHook` 的子类——在 LLM 调用前检查预算，当策略为 "block" 且预算超限时抛出 `BudgetExceededError`
- [x] 3.6 编写测试：预算 CRUD、分层解析、执行钩子（block vs alert）、周期重置

## 4. 成本异常检测与预测 (6.4)

- [x] 4.1 实现每个模型每日支出的 Z-score 异常检测（30 天滚动窗口，可配置阈值默认 2.5）
- [x] 4.2 使用每日支出数据的线性回归实现支出预测——返回预测金额、置信区间、预测超支
- [x] 4.3 实现费用分摊报告生成——按 Agent/项目维度聚合，包含每个模型的分解和环比比较
- [x] 4.4 添加基于 Z-score 幅度的异常严重性分类（`info`/`warn`/`critical`）
- [x] 4.5 添加冷启动保护——当历史数据不足 7 天时跳过异常检测
- [x] 4.6 编写测试：正常消费不被标记、消费高峰被检测、冷启动跳过、预测低于/超过预算、费用分摊聚合

## 5. 成本管理 API (6.4)

- [x] 5.1 在 `src/hecate/api/management/cost_management.py` 中创建 API 路由：`POST/GET/PUT/DELETE /api/models/cost/budgets`、`GET /api/models/cost/anomalies`、`GET /api/models/cost/forecast`、`GET /api/models/cost/chargeback`
- [x] 5.2 在 `src/hecate/main.py` 中注册成本管理路由器
- [x] 5.3 在 `src/hecate/core/config.py` 中添加 `CostManagementSettings`（异常阈值、滚动窗口天数、默认策略）
- [x] 5.4 编写 API 集成测试：预算生命周期、异常列表、预测检索、费用分摊报告

## 6. 推理端点管理 (6.5)

- [x] 6.1 在 `src/hecate/model_hub/inference_manager.py` 中定义 `InferenceBackendABC`，包含抽象方法 `health_check(endpoint)` 和 `invoke(endpoint, request)`
- [x] 6.2 实现 `OpenAICompatibleBackend`——通过 `/health` 处理健康检查，通过 `/v1/models` 处理模型列表，通过 `/v1/chat/completions` 处理调用
- [x] 6.3 在 `src/hecate/models/` 中创建 `InferenceEndpointModel`，字段包括：url、model_id、backend_type、auth_config、health_status、last_health_at、workspace_id
- [x] 6.4 为 `inference_endpoints` 表创建 Alembic 迁移
- [x] 6.5 实现 `InferenceManager` 服务——端点 CRUD、定期健康轮询（asyncio 任务，可配置间隔）、重试逻辑（标记为不可达前尝试 3 次）
- [x] 6.6 实现基于健康状态的路由——仅将调用路由到健康端点，当所有端点不可达时回退到替代提供商
- [x] 6.7 实现从暴露 `/metrics` 的端点抓取 Prometheus 指标（TTFT、吞吐量、错误率）
- [x] 6.8 在 `src/hecate/api/management/inference.py` 中创建 API 路由：`POST/GET/PUT/DELETE /api/inference/endpoints`、`GET /api/inference/endpoints/{id}/health`
- [x] 6.9 在 `src/hecate/main.py` 中注册推理路由器
- [x] 6.10 编写测试：ABC 不可实例化、OpenAICompatibleBackend 健康检查、端点 CRUD、健康轮询、不可达标记、仅路由到健康端点

## 7. 微调流水线 (6.6)

- [x] 7.1 在 `src/hecate/model_hub/fine_tuning.py` 中定义 `FineTuningBackendABC`，包含抽象方法：`submit_job`、`poll_status`、`cancel_job`、`get_result`
- [x] 7.2 在 `src/hecate/models/` 中创建 `DatasetModel`，字段包括：name、description、format、version、row_count、schema_preview、file_storage_url、workspace_id
- [x] 7.3 在 `src/hecate/models/` 中创建 `FineTuningJobModel`，字段包括：dataset_id、base_model、provider、provider_job_id、status、config、result_model_id、metrics、error_message、workspace_id
- [x] 7.4 为 `datasets` 和 `fine_tuning_jobs` 表创建 Alembic 迁移
- [x] 7.5 实现 `DatasetService`——CRUD、文件上传到 MinIO、版本控制、预览（前 10 行）
- [x] 7.6 实现 `OpenAIFineTuningBackend`——通过 `/v1/files` 上传，通过 `/v1/fine_tuning/jobs` 创建任务、轮询状态、检索结果
- [x] 7.7 实现 `InMemoryFineTuningBackend`——模拟任务生命周期（queued → running → succeeded）的测试存根
- [x] 7.8 实现 `FineTuningService`——编排后端调用、持久化任务状态、异步轮询循环（可配置间隔）
- [x] 7.9 实现一键部署——任务成功后，在 `ModelRegistryModel` 中注册微调模型，元数据关联到基础模型
- [x] 7.10 在 `src/hecate/api/management/fine_tuning.py` 中创建 API 路由：数据集 CRUD + 上传、任务 CRUD + 提交/取消、`POST /api/fine-tuning/jobs/{id}/deploy`
- [x] 7.11 在 `src/hecate/main.py` 中注册微调路由器
- [x] 7.12 编写测试：ABC 不可实例化、数据集 CRUD + 上传、InMemory 后端任务生命周期、OpenAI 后端 API 调用（模拟）、部署注册

## 8. 监控聚合后端 (O10+G4)

- [x] 8.1 在 `src/hecate/model_hub/monitoring.py` 中实现 `MonitoringService`——将 TraceModel 聚合成每个模型的性能指标（平均延迟、TTFT、错误率、请求数、成本）
- [x] 8.2 实现具有可配置粒度（hourly/daily/weekly）和日期范围过滤的时间序列聚合
- [x] 8.3 实现模型比较聚合——多个模型的并排指标
- [x] 8.4 实现性能漂移检测——每日延迟和错误率的 Z-score（与成本异常检测相同的算法，不同的指标输入）
- [x] 8.5 扩展成本仪表板 API，添加每个模型的趋势时间序列端点（`GET /api/cost-dashboard/trends?group_by=model`）
- [x] 8.6 在 `src/hecate/api/management/monitoring.py` 中创建 API 路由：`GET /api/monitoring/models/{id}/performance`、`GET /api/monitoring/models/compare`、`GET /api/monitoring/models/{id}/drift`
- [x] 8.7 在 `src/hecate/main.py` 中注册监控路由器
- [x] 8.8 编写测试：性能聚合、时间序列粒度、比较矩阵、延迟峰值时的漂移检测

## 9. 监控控制台前端 (O10+G4)

- [x] 9.1 从 shadcn/ui 将 Recharts Chart 组件添加到 `web/`（LineChart、BarChart、DonutChart 包装器）
- [x] 9.2 在 `web/src/app/(dashboard)/settings/models/monitoring/page.tsx` 创建监控仪表板页面——模型选择器、时间范围选择器、指标切换、趋势折线图（延迟、成本、错误率）
- [x] 9.3 创建模型比较视图组件——每个模型为行、指标列为列、能力徽章的表格
- [x] 9.4 在 `web/src/app/(dashboard)/settings/models/cost-analysis/page.tsx` 创建成本分析页面——每个模型的成本柱状图、预算利用率仪表盘、异常时间线、预测投影
- [x] 9.5 添加漂移告警推送组件——最近的漂移事件列表，附带严重性指示器
- [x] 9.6 在仪表板布局中添加预算超限警告横幅，当工作区支出超过月度预算的 80% 时显示
- [x] 9.7 更新侧边栏导航，包含到监控仪表板和成本分析页面的链接
- [x] 9.8 编写前端组件测试：使用模拟数据的图表渲染、模型选择器交互、比较表格填充

## 10. 集成与验证

- [x] 10.1 验证所有新模块通过 `ruff check src/hecate/ tests/`
- [x] 10.2 验证所有新模块通过 `ruff format --check src/ tests/`
- [x] 10.3 验证所有新模块通过 `mypy src/`
- [x] 10.4 运行所有新测试文件的定向测试套件
- [x] 10.5 运行完整测试套件（`python -m pytest tests/ -q`）——验证零回归
- [x] 10.6 更新 `docs/features/feature-catalog.md`——将 6.4、6.11、6.5、6.6、O10+G4 标记为 ✅，更新 P3 统计
- [x] 10.7 更新 `docs/features/roadmap.md`——将 Sprint 5 Model Hub 功能标记为 ✅ 完成，更新里程碑 M5 复选框
