## 1. 评估框架修复

- [x] 1.1 向 `EvaluationItemModel` 添加 `generated_answer` 列（可空 TEXT，默认 NULL）并更新 Pydantic 模式（`EvaluationItemCreateSchema`、`EvaluationItemReadSchema`）
- [ ] 1.2 创建 Alembic 迁移：向 `evaluation_items` 表添加 `generated_answer` 列 *（阻塞：需要运行 PostgreSQL）*
- [x] 1.3 向 `EvaluationRunCreateSchema` 添加 `answer_source` 字段（枚举：manual、pipeline、auto；默认 auto）及 `types.py` 中的 `AnswerSource` 枚举
- [x] 1.4 重构 `EvaluationEngine.run()` 以接受 `answer_source` 参数，并在可用时使用项的 `generated_answer` 而非硬编码的空字符串
- [x] 1.5 在 `EvaluationEngine` 中实现管道答案生成——对于 RAG 评估，使用项查询调用 `KnowledgeBaseService.search()`，使用检索到的上下文通过 LLM 服务生成答案
- [x] 1.6 更新评估运行 API 端点以接受并传递 `answer_source` 参数
- [x] 1.7 编写/更新测试：带手动答案的 `EvaluationEngine`、管道模式、自动回退

## 2. 代理评估器（新增）

- [x] 2.1 在 `services/evaluation/agent_evaluators.py` 中实现 `ToolCallAccuracyEvaluator`——LLM-as-Judge 比较实际与预期的工具调用，对选择 + 参数准确性评分
- [x] 2.2 将 `TOOL_CALL_ACCURACY_PROMPT` 添加到 `services/evaluation/prompts.py`
- [x] 2.3 在 `services/evaluation/agent_evaluators.py` 中实现 `TaskCompletionEvaluator`——LLM-as-Judge 从查询 + 响应评估任务完成情况
- [x] 2.4 将 `TASK_COMPLETION_PROMPT` 添加到 `services/evaluation/prompts.py`
- [x] 2.5 在 `api/evaluation.py` 的 `_EVALUATOR_REGISTRY` 中注册两个新评估器
- [x] 2.6 为 `ToolCallAccuracyEvaluator` 编写测试：正确的工具、错误的工具、无工具调用
- [x] 2.7 为 `TaskCompletionEvaluator` 编写测试：完全完成、部分完成、未尝试

## 3. 审计日志——模型与存储

- [x] 3.1 在新文件 `models/audit.py` 中定义 `AuditAction` 枚举（6 个模块 × 操作：AUTH、AGENT、WORKFLOW、KNOWLEDGE、TOOL、SYSTEM）
- [x] 3.2 在 `models/audit.py` 中创建 `AuditLogModel`——分区表，包含所有规范字段（org_id、workspace_id、user_id、action、resource_type、resource_id、request_method、request_path、response_status、ip_address、user_agent、success、error_code、metadata JSONB）
- [x] 3.3 创建 Pydantic 模式：`AuditLogReadSchema`、`AuditLogQuerySchema`、`AuditLogExportSchema`
- [x] 3.4 在新文件 `services/audit/store.py` 中定义 `AuditStore` ABC，带方法：`write()`、`query()`、`export()`、`archive()`
- [x] 3.5 实现 `DatabaseAuditStore`——写入分区 PostgreSQL，带过滤器的查询，CSV/JSON 导出
- [ ] 3.6 创建 Alembic 迁移：安装 `pg_partman` 扩展，创建分区 `audit_logs` 表，为月度自动创建配置 pg_partman *（阻塞：需要运行 PostgreSQL）*

## 4. 审计日志——中间件与写入器

- [x] 4.1 在新文件 `api/middleware.py` 中实现 `AuditMiddleware`（BaseHTTPMiddleware）——提取 AuthContext，记录方法/路径/状态/时间，入队审计事件
- [x] 4.2 使用 `asyncio.Queue` 实现异步批量写入器（`AuditBatchWriter`）——以 N 批次排空队列，通过 `DatabaseAuditStore` 插入
- [x] 4.3 在 `main.py` 中注册 `AuditMiddleware`（在 CORS 中间件之前以确保正确的顺序）
- [x] 4.4 在应用生命周期（`main.py`）中启动/停止 `AuditBatchWriter`
- [x] 4.5 为 `AuditMiddleware` 编写测试：已认证请求、未认证请求、排除路径、错误响应

## 5. 审计日志——安全策略引擎

- [x] 5.1 在新文件 `services/audit/policy.py` 中定义 `AuditSecurityPolicy` 数据类和 `PolicyAction` 枚举（ALERT、BLOCK、RATE_LIMIT）
- [x] 5.2 实现 `PolicyEngine`——使用时间窗口计数评估审计事件与已注册策略
- [x] 5.3 实现 3 个内置策略：`bulk_delete_protection`、`off_hours_sensitive_ops`、`unusual_ip_detection`
- [x] 5.4 将 `PolicyEngine` 集成到 `AuditBatchWriter` 中——在批量插入前评估事件，记录违规
- [x] 5.5 为每个策略编写测试：触发条件、不触发条件

## 6. 审计日志——API 与归档

- [x] 6.1 在新文件 `services/audit/service.py` 中创建 `AuditService`——包装 `AuditStore` 用于业务逻辑（带租户过滤的查询、导出、统计）
- [x] 6.2 在新文件 `api/audit.py` 中创建 API 路由：`GET /api/audit-logs`（分页查询）、`GET /api/audit-logs/export`（CSV/JSON 导出）、`GET /api/audit-logs/stats`（聚合）
- [x] 6.3 在 `main.py` 中注册审计路由
- [x] 6.4 实现 MinIO 归档工作者——删除旧分区，导出到 MinIO 作为压缩 JSON
- [x] 6.5 向 `core/config.py` 添加保留配置（`AUDIT_RETENTION_DAYS`、`AUDIT_ARCHIVAL_ENABLED`、`AUDIT_MINIO_BUCKET`）
- [x] 6.6 编写集成测试：完整审计流程（请求 → 中间件 → 队列 → DB → 查询）

## 7. 计划任务——模型与调度器

- [x] 7.1 在新文件 `models/scheduled_task.py` 中创建 `ScheduledTaskModel`——所有规范字段（cron_expression、agent_id、workflow_id、execution_config JSONB、state、max_concurrent_runs、catch_up、timezone、next_run_at、last_run_at）
- [x] 7.2 在同一文件中创建 `ScheduledTaskExecutionModel`——task_id FK、started_at、completed_at、status、result_summary JSONB、error_message、duration_ms、triggered_by
- [x] 7.3 为两个模型创建 Pydantic 模式（Create、Update、Read）
- [x] 7.4 将 `apscheduler~=3.10` 添加到 `pyproject.toml` 的 `[scheduling]` 可选依赖组
- [x] 7.5 在新文件 `services/scheduling/manager.py` 中实现 `ScheduleManager`——包装带 PostgreSQL JobStore 的 APScheduler，管理调度 CRUD、cron 表达式解析和验证
- [ ] 7.6 创建 Alembic 迁移：`scheduled_tasks` 和 `scheduled_task_executions` 表 *（阻塞：需要运行 PostgreSQL）*

## 8. 计划任务——执行与多节点

- [x] 8.1 在 `ScheduleManager._execute_with_lock()` 中实现咨询锁获取——`pg_try_advisory_lock(hashint8(task_id, scheduled_time))`，完成时释放
- [x] 8.2 实现执行绑定——`AgentExecutor` 和 `WorkflowExecutor`，创建对话/会话、调用代理或工作流、捕获结果摘要
- [x] 8.3 实现 `max_concurrent_runs` 强制——在触发新执行前检查活动执行数，如果达到限制则跳过
- [x] 8.4 实现 `catch_up` 逻辑——当调度器暂停后恢复时，如果 `catch_up=true` 则排队未执行的执行
- [x] 8.5 将 `ScheduleManager` 集成到应用生命周期中——启动时启动调度器，关闭时优雅停止
- [x] 8.6 编写测试：cron 验证、状态机转换、咨询锁竞争

## 9. 计划任务——API

- [x] 9.1 在新文件 `services/scheduling/service.py` 中创建 `ScheduledTaskService`——CRUD + 状态转换 + 手动触发
- [x] 9.2 在新文件 `api/schedules.py` 中创建 API 路由：POST（创建）、GET（列表）、GET/{id}、PUT/{id}、DELETE/{id}、POST/{id}/trigger、PUT/{id}/pause、PUT/{id}/resume、GET/{id}/executions
- [x] 9.3 在 `main.py` 中注册调度路由
- [x] 9.4 编写测试：创建调度、暂停/恢复、手动触发、执行历史查询

## 10. 验证

- [x] 10.1 运行 `ruff check src/hecate/ tests/`——零错误
- [x] 10.2 运行 `ruff format --check src/ tests/`——零错误
- [x] 10.3 运行 `mypy src/`——零错误
- [x] 10.4 运行 `python -m pytest tests/ -q`——所有测试通过（无新失败）
- [x] 10.5 更新 `docs/features/feature-catalog.md`——将 7.1、7.2、8.7、13.9 标记为 ✅
- [x] 10.6 更新 `docs/features/roadmap.md`——更新 P2 统计为 53/57（93%）
