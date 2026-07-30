## Context — 上下文

Hecate 是一个企业级、自托管、模型无关的代理平台。P2（企业就绪）已完成 49/57（86%）。此变更交付 4 个后端特性，将 P2 推至 53/57（93%）

**当前状态**：
- 评估框架（7.1/7.2）：Evaluator ABC、4 个 RAG 评估器（基于 Ragas）、3 个代理评估器（LLM-as-Judge）、EvaluationEngine、ORM 模型和 API 都已实现。然而，`EvaluationEngine.run()` 硬编码了 `generated_answer=""`，使得所有分数毫无意义。`EvaluationItemModel` 缺少 `generated_answer` 字段。`ToolCallAccuracyEvaluator` 和 `TaskCompletionEvaluator` 缺失
- 审计日志（8.7）：没有 AuditLog 模型、没有审计中间件、没有审计 API。现有的可观测性基础设施（TraceModel、EventStore、StructuredLogger）覆盖执行追踪，而非用户操作审计
- 计划任务（13.9）：`MetaAgentScheduler` 仅用于元代理（简单的基于间隔，无 cron，无多节点）。没有 ScheduledTask 模型、无 cron 支持、无代理/工作流绑定

**架构约束**：
- 引擎层零外部依赖（仅 jsonschema 例外）。新特性位于 services/ 和 api/ 层
- 多租户层级：OrganizationModel → WorkspaceModel → Resources
- AuthContext 通过 FastAPI 依赖注入提供 `user_id`、`org_id`、`workspace_id`、`role`
- 所有 ORM 模型继承 BaseModel（UUID PK、`created_at`/`updated_at`、软删除 `deleted`/`deleted_at`）
- SQLAlchemy 模型的 `metadata_` 别名模式

## Goals / Non-Goals — 目标 / 非目标

**目标：**
- 修复评估引擎以支持实际的答案生成（手动 + RAG 管道集成）
- 添加 2 个缺失的代理评估指标（ToolCallAccuracy、TaskCompletion）
- 交付面向 SaaS 的审计日志，包含 6 个模块、约 70 种操作类型、月度分区、MinIO 归档和安全策略引擎
- 交付多节点计划任务执行，包含 cron 表达式、PostgreSQL JobStore 和咨询锁

**非目标：**
- 任何这些特性的前端 UI（Canvas、Model Playground 是单独的 P2 前端工作）
- 用于审计日志的基于 ML 的异常检测（P3）
- 用于计划任务的 Temporal 集成（P3）
- 40+ 评估器扩展（P3 特性 7.2a）
- 合规报告（SOC 2、GDPR 模板）——基础设施已铺设，但模板为 P3

## Decisions — 决策

### D1：审计存储架构——ABC + 单表 + 分区

**决策**：`AuditStore` ABC 以 `DatabaseAuditStore` 为默认实现。PostgreSQL 月度范围分区基于 `created_at`。超过可配置阈值的分区归档到 MinIO

**理由**：
- ABC 允许未来实现 `DualAuditStore`（PG + Elasticsearch）或 `ExternalAuditStore`（SLS、Datadog）而不触及业务逻辑
- 月度分区高效处理每天 10 万-100 万条日志；单个分区可以根据保留策略删除
- MinIO 已在基础设施栈中（docker-compose）

**考虑的替代方案**：
- 单个无分区表：更简单但扩展时性能下降；VACUUM 开销增长
- Elasticsearch 作为主存储：强大但为自托管用户增加运维复杂性
- Salesforce 风格的 3 层存储（ELF + ELO + RTEM）：对自托管来说过度设计；我们的单表 + 分区 + 归档实现了等效能力

### D2：审计捕获——FastAPI 中间件 + 服务装饰器

**决策**：两层捕获：
1. `AuditMiddleware`（BaseHTTPMiddleware）——自动捕获所有 API 请求，提取 AuthContext，记录方法/路径/状态/时间
2. `@audit_action(action=...)` 装饰器——在服务级别丰富 resource_type、resource_id、业务元数据

**理由**：中间件提供完整覆盖（没有路由可以绕过审计）。装饰器添加语义丰富。Dify 仅在服务级别捕获（覆盖不完整）。Salesforce 在平台级别捕获（自托管不可行）

**排除项**：`/health`、`/metrics`、静态资产、OPTIONS 请求

### D3：计划任务——APScheduler + PostgreSQL JobStore + 咨询锁

**决策**：APScheduler 3.x 使用 `PostgreSQLJobStore` 进行作业持久化。多个调度器实例（每个应用节点一个）通过 `pg_try_advisory_lock()` 竞争作业执行

**理由**：
- APScheduler 轻量级，不需要外部服务依赖（不像 Celery+Redis 或 Temporal）
- PostgreSQL JobStore 跨重启持久化作业
- 咨询锁防止多节点部署中的重复执行
- APScheduler 原生支持 cron 表达式、间隔和一次性触发器

**考虑的替代方案**：
- Celery Beat + Redis：更重的基础设施要求；Redis 不在当前栈中
- Temporal：已在代码库中作为占位符，但完全集成是 P3
- 单调度器 + 多 Worker：单点故障

### D4：评估引擎修复——双模式（手动 + 管道）

**决策**：`EvaluationEngine.run()` 接受可选的 `answer_source` 参数：
- `"manual"`——使用 `EvaluationItemModel` 中的 `generated_answer`（新字段）
- `"pipeline"`——在评估前调用 RAG 管道或代理会话来生成答案
- `"auto"`（默认）——如果 `generated_answer` 存在则使用，否则回退到管道调用

**理由**：同时支持预标记数据集（回归测试）和实时管道评估（质量监控）。`generated_answer` 字段添加是向后兼容的（可空，默认空）

### D5：审计安全策略引擎——基于规则（P2）→ 基于 ML（P3）

**决策**：P2 实现一个简单的规则引擎，带 3 个内置策略：
1. `bulk_delete_protection`——同一用户在 1 分钟内删除 5 个以上资源时告警
2. `off_hours_sensitive_ops`——敏感操作在营业时间外发生时告警
3. `unusual_ip_detection`——从未识别的 IP 登录时告警

策略在异步写入器中针对每个审计事件同步评估。匹配触发结构化日志警告（P3 中可选 webhook 通知）

**理由**：基于规则的策略覆盖最常见的企业安全关注点。ML 异常检测需要训练数据，更适合 P3 当审计数据积累后

### D6：审计日志分区——使用 pg_partman 手动管理

**决策**：使用 `pg_partman` 扩展自动创建月度分区。在 Alembic 迁移中包含设置。保留策略删除超过可配置阈值（默认 365 天）的分区

**理由**：pg_partman 是一个维护良好的 PostgreSQL 扩展，自动化分区生命周期管理。随 PostgreSQL 16 contrib 提供或作为独立扩展

## Risks / Trade-offs — 风险 / 权衡

- **[风险] 审计中间件为每个 API 请求增加延迟** → 缓解措施：异步批量写入器使用 `asyncio.Queue`；中间件仅入队，从不阻塞数据库写入。目标：每个请求 <1ms 开销
- **[风险] 高扩展下 APScheduler 咨询锁竞争** → 缓解措施：咨询锁轻量级（整数比较）；竞争仅在调度触发时发生（通常每分钟/每小时一次）。如果成为瓶颈，在 P3 中迁移到 Temporal
- **[风险] pg_partman 并非在所有 PostgreSQL 部署中可用** → 缓解措施：提供手动分区创建作为回退；记录两种方法
- **[风险] 大数据集的 RAG 管道集成可能很慢** → 缓解措施：在后台运行评估并带进度追踪；支持每项超时
- **[权衡] 单一审计表限制查询灵活性 vs 按模块分开表** → 已接受：分区 + 适当索引为自托管规模提供足够的查询性能。单表简化了保留管理和归档

## Migration Plan — 迁移计划

1. **Alembic 迁移**：向 `evaluation_items` 添加 `generated_answer` 列，创建 `audit_logs`（分区表）、`scheduled_tasks`、`scheduled_task_executions` 表，安装 `pg_partman` 扩展
2. **部署**：在 `main.py` 中注册新中间件，在应用生命周期中启动 APScheduler
3. **回滚**：从 `main.py` 移除中间件，删除新表/列。这些是增量变更，没有数据损坏风险
4. **无破坏性变更**：所有变更都是增量的。现有评估 API 继续工作（新的 `generated_answer` 列可为空）

## Open Questions — 未决问题

- 审计日志归档到 MinIO 应该是同步还是异步的？（倾向于异步——归档工作进程按计划运行）
- `AuditSecurityPolicy` 违规应存储在单独的表中还是仅记录日志？（倾向于：记录日志 + 可选 webhook，P3 中单独表）
- 计划任务执行是否应支持带退避的重试？（倾向于：是的，可配置的 `max_retries` + `retry_delay`）
