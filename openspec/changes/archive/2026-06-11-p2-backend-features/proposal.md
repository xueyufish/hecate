## Why — 原因

P2（企业就绪）已完成 49/57（86%）。在剩余工作转向仅前端（Canvas、Model Playground）之前，还有四个后端特性需要完成。现在完成这些将把 P2 推至 53/57（93%），并解除 P3 评估扩展的阻塞。

此外，当前的评估框架已有评估器实现，但端到端流程存在问题——`EvaluationEngine.run()` 硬编码了 `generated_answer=""`，使得所有针对空字符串的评估分数毫无意义。

## What Changes — 变更内容

- **7.1 RAG 评估（修复）**：将 `EvaluationEngine` 接入 RAG 管道以自动生成答案；向 `EvaluationItemModel` 添加 `generated_answer` 字段，使用户也可以手动提供答案
- **7.2 代理评估（修复+扩展）**：添加 `ToolCallAccuracyEvaluator` 和 `TaskCompletionEvaluator`（规范引用"任务完成率、工具调用准确性、响应质量"）；共享 7.1 的 `generated_answer` 修复
- **8.7 审计日志（新增）**：面向 SaaS 的审计日志——`AuditStore` ABC + PostgreSQL 月度分区表 + 异步批量写入器 + MinIO 冷归档 + `AuditSecurityPolicy` 规则引擎（3 个内置策略）+ 查询/导出的 REST API。通过 FastAPI 中间件全量捕获所有 API 操作，组织为 6 个模块（AUTH、AGENT、WORKFLOW、KNOWLEDGE、TOOL、SYSTEM），约 70 种操作类型。受 Salesforce Event Monitoring + Transaction Security 启发，为自托管而扩展
- **13.9 计划任务（新增）**：基于 Cron 触发的代理/工作流执行，支持多节点——`APScheduler` + PostgreSQL JobStore + 用于分布式调度的咨询锁、cron 表达式支持、调度状态机（ACTIVE/PAUSED/COMPLETED）、`max_concurrent_runs` 和 `catch_up` 语义（受 Google Vertex AI Scheduler 启发）、结果持久化和通道推送

## Capabilities — 能力

### New Capabilities — 新增能力
- `audit-logs`：全量用户操作审计追踪，采用 SaaS 就绪的存储架构（AuditStore ABC、月度分区、MinIO 归档、SecurityPolicy 规则引擎、6 个模块 × 约 70 种操作类型、REST 查询/导出 API）
- `scheduled-tasks`：基于 Cron 的代理/工作流调度，支持多节点分布式执行（APScheduler + PostgreSQL JobStore + 咨询锁、调度状态机、并发控制）

### Modified Capabilities — 修改的能力
- `rag-evaluation`：将评估引擎接入 RAG 管道以实现端到端答案生成；向 `EvaluationItemModel` 添加 `generated_answer` 字段
- `agent-evaluation`：添加 `ToolCallAccuracyEvaluator` 和 `TaskCompletionEvaluator`；共享来自 rag-evaluation 的 `generated_answer` 修复
- `evaluation-framework`：修复 `EvaluationEngine.run()` 以使用实际的 `generated_answer` 而非硬编码的空字符串；支持手动答案和管道自动答案两种模式

## Impact — 影响

- **Models**：新的 `AuditLogModel`（月度分区）、`ScheduledTaskModel`、`ScheduledTaskExecutionModel`。修改 `EvaluationItemModel`（添加 `generated_answer` 列）
- **API**：`/api/audit-logs` 和 `/api/schedules` 下的新路由。修改评估运行端点以支持管道集成
- **Services**：新的 `AuditService`、`ScheduledTaskService`。修改 `EvaluationEngine`、`EvaluationDatasetService`
- **Middleware**：新的 `AuditMiddleware`（FastAPI BaseHTTPMiddleware）用于自动 API 操作捕获
- **Dependencies**：`apscheduler~=3.10`（新的可选 `[scheduling]` 组）。用于分区管理的 `pg_partman` 扩展
- **Database**：3 个新表 + 1 列添加 + 分区设置的新 Alembic 迁移
- **Infrastructure**：用于冷审计日志归档的 MinIO 集成
