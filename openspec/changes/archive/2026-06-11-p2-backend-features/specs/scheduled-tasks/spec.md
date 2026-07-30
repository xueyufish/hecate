## ADDED Requirements — 新增需求

### 需求：ScheduledTask 模型
系统应将计划任务定义持久化到 `scheduled_tasks` 表中，字段包括：`id`（UUID PK）、`org_id`（UUID，NOT NULL）、`workspace_id`（UUID，可空）、`name`（VARCHAR(255)，NOT NULL）、`description`（TEXT，可空）、`cron_expression`（VARCHAR(100)，NOT NULL）、`agent_id`（UUID，可空——如果绑定到代理）、`workflow_id`（UUID，可空——如果绑定到工作流）、`execution_config`（JSONB——执行参数）、`state`（VARCHAR(20)，可取：ACTIVE、PAUSED、COMPLETED、ERROR）、`max_concurrent_runs`（INTEGER，默认 1）、`catch_up`（BOOLEAN，默认 false）、`timezone`（VARCHAR(50)，默认 "UTC"）、`next_run_at`（TIMESTAMPTZ，可空）、`last_run_at`（TIMESTAMPTZ，可空）、`enabled`（BOOLEAN，默认 true），外加继承的 BaseModel 字段

#### 场景：创建计划任务
- **当** 发送 POST 请求到 `/api/schedules`，携带 `{"name": "daily-report", "cron_expression": "0 9 * * *", "agent_id": "..."}`
- **则** 系统应创建 `state=ACTIVE` 的 ScheduledTask 记录，计算 `next_run_at`，返回 201

#### 场景：验证 cron 表达式
- **当** 使用无效的 cron 表达式 `"invalid"` 创建计划任务
- **则** 系统应返回 422，附带解释 cron 格式的错误详情

### 需求：ScheduledTaskExecution 模型
系统应将执行历史持久化到 `scheduled_task_executions` 表中，字段包括：`id`（UUID PK）、`task_id`（UUID，指向 scheduled_tasks 的 FK）、`started_at`（TIMESTAMPTZ）、`completed_at`（TIMESTAMPTZ，可空）、`status`（VARCHAR(20)：SUCCESS、FAILED、TIMEOUT、SKIPPED）、`result_summary`（JSONB，可空）、`error_message`（TEXT，可空）、`duration_ms`（INTEGER，可空）、`triggered_by`（VARCHAR(20)：cron、manual），外加继承的 BaseModel 字段

#### 场景：记录成功执行
- **当** 计划任务执行成功完成
- **则** 系统应创建 `status=SUCCESS`、`completed_at` 和 `duration_ms` 的 ScheduledTaskExecution 记录

### 需求：带 PostgreSQL JobStore 的 APScheduler
系统应使用 APScheduler 3.x 配合 PostgreSQL 支持的 `JobStore` 进行持久化作业管理。作业应使用标准 Unix cron 表达式进行调度。调度器应在应用生命周期中启动，并在关闭时优雅停止

#### 场景：调度器在重启后存活
- **当** 应用被重启
- **则** 所有先前已调度的任务应基于其 cron 表达式恢复执行，无需手动干预

#### 场景：优雅关闭
- **当** 应用收到关闭信号
- **则** 调度器应完成任何正在进行的执行并停止接受新触发

### 需求：通过咨询锁实现多节点分布式执行
当多个应用实例并发运行时，每个实例应运行自己的 APScheduler，但在执行作业前，实例应通过 `pg_try_advisory_lock(hashint8(task_id, scheduled_time))` 获取 PostgreSQL 咨询锁。只有获取到锁的实例才应执行作业；其他实例应跳过

#### 场景：两个节点，一个作业
- **当** 两个应用实例同时到达计划的触发时间
- **则** 仅一个实例应执行作业；另一个应跳过并记录调试消息

#### 场景：执行后释放锁
- **当** 作业执行完成（成功或失败）
- **则** 咨询锁应被释放，以便下一个触发器可以继续进行

### 需求：调度状态机
每个计划任务应有一个状态机：ACTIVE → PAUSED（用户暂停）、PAUSED → ACTIVE（用户恢复）、ACTIVE → COMPLETED（用户取消或 end_time 到达）、ACTIVE → ERROR（执行反复失败）。状态转换应被验证

#### 场景：暂停调度
- **当** 发送 PUT 请求到 `/api/schedules/{id}/pause`
- **则** 系统应设置 `state=PAUSED`，停止调度作业，返回更新后的任务

#### 场景：恢复调度
- **当** 发送 PUT 请求到 `/api/schedules/{id}/resume`
- **则** 系统应设置 `state=ACTIVE`，重新计算 `next_run_at`，恢复调度

### 需求：调度管理 API
系统应暴露 REST 端点：`POST /api/schedules`（创建）、`GET /api/schedules`（列表，分页）、`GET /api/schedules/{id}`（获取）、`PUT /api/schedules/{id}`（更新 cron/配置）、`DELETE /api/schedules/{id}`（软删除）、`POST /api/schedules/{id}/trigger`（手动触发）、`PUT /api/schedules/{id}/pause`、`PUT /api/schedules/{id}/resume`、`GET /api/schedules/{id}/executions`（执行历史）

#### 场景：手动触发
- **当** 发送 POST 请求到 `/api/schedules/{id}/trigger`
- **则** 系统应立即执行计划任务，无论其 cron 调度如何，创建 `triggered_by="manual"` 的执行记录

#### 场景：更新 cron 表达式
- **当** PUT 请求更新活动调度的 `cron_expression`
- **则** 系统应重新调度 APScheduler 作业并更新 `next_run_at`

### 需求：带 max_concurrent_runs 的并发控制
系统应对每个计划任务强制执行 `max_concurrent_runs`。如果新触发器触发时 `max_concurrent_runs` 个执行正在进行中，系统应跳过触发器并记录警告。当 `catch_up=true` 时，系统应为稍后执行排队未执行的

#### 场景：达到最大并发运行数
- **当** 计划任务的 `max_concurrent_runs=1` 且下一次触发器触发时仍有执行在进行中
- **则** 系统应跳过触发器并创建 `status=SKIPPED` 的执行记录

### 需求：执行结果绑定
当计划任务触发代理或工作流执行时，系统应捕获执行结果（对话消息、工作流输出）并在 `ScheduledTaskExecution.result_summary` 中存储摘要。执行应使用任务创建者的工作区上下文作为已认证操作运行

#### 场景：代理执行结果已捕获
- **当** 计划任务触发代理执行
- **则** 系统应创建对话、执行代理，并将响应摘要存储在执行记录中
