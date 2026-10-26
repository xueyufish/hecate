# scheduled-tasks Specification

## Purpose

Defines scheduled tasks: the task and execution models, APScheduler with a PostgreSQL job store, multi-node distributed execution via advisory locks, the schedule state machine, management APIs, concurrency control, and execution result binding.

## Requirements
### Requirement: ScheduledTask model
The system SHALL persist scheduled task definitions in a `scheduled_tasks` table with fields: `id` (UUID PK), `org_id` (UUID, NOT NULL), `workspace_id` (UUID, nullable), `name` (VARCHAR(255), NOT NULL), `description` (TEXT, nullable), `cron_expression` (VARCHAR(100), NOT NULL), `agent_id` (UUID, nullable — if binding to agent), `workflow_id` (UUID, nullable — if binding to workflow), `execution_config` (JSONB — execution parameters), `state` (VARCHAR(20), one of: ACTIVE, PAUSED, COMPLETED, ERROR), `max_concurrent_runs` (INTEGER, default 1), `catch_up` (BOOLEAN, default false), `timezone` (VARCHAR(50), default "UTC"), `next_run_at` (TIMESTAMPTZ, nullable), `last_run_at` (TIMESTAMPTZ, nullable), `enabled` (BOOLEAN, default true), plus BaseModel inherited fields.

#### Scenario: Create scheduled task
- **WHEN** a POST request is sent to `/api/schedules` with `{"name": "daily-report", "cron_expression": "0 9 * * *", "agent_id": "..."}`
- **THEN** the system SHALL create a ScheduledTask record with `state=ACTIVE`, calculate `next_run_at`, and return 201

#### Scenario: Validate cron expression
- **WHEN** a scheduled task is created with an invalid cron expression `"invalid"`
- **THEN** the system SHALL return 422 with error detail explaining the cron format

### Requirement: ScheduledTaskExecution model
The system SHALL persist execution history in a `scheduled_task_executions` table with fields: `id` (UUID PK), `task_id` (UUID, FK to scheduled_tasks), `started_at` (TIMESTAMPTZ), `completed_at` (TIMESTAMPTZ, nullable), `status` (VARCHAR(20): SUCCESS, FAILED, TIMEOUT, SKIPPED), `result_summary` (JSONB, nullable), `error_message` (TEXT, nullable), `duration_ms` (INTEGER, nullable), `triggered_by` (VARCHAR(20): cron, manual), plus BaseModel inherited fields.

#### Scenario: Record successful execution
- **WHEN** a scheduled task execution completes successfully
- **THEN** the system SHALL create a ScheduledTaskExecution record with `status=SUCCESS`, `completed_at`, and `duration_ms`

### Requirement: APScheduler with PostgreSQL JobStore
The system SHALL use APScheduler 3.x with a PostgreSQL-backed `JobStore` for persistent job management. Jobs SHALL be scheduled using standard Unix cron expressions. The scheduler SHALL start in the application lifespan and stop gracefully on shutdown.

#### Scenario: Scheduler survives restart
- **WHEN** the application is restarted
- **THEN** all previously scheduled tasks SHALL resume execution based on their cron expressions without manual intervention

#### Scenario: Graceful shutdown
- **WHEN** the application receives a shutdown signal
- **THEN** the scheduler SHALL complete any in-progress execution and stop accepting new triggers

### Requirement: Multi-node distributed execution via advisory locks
When multiple application instances run concurrently, each instance SHALL run its own APScheduler, but before executing a job, the instance SHALL acquire a PostgreSQL advisory lock via `pg_try_advisory_lock(hashint8(task_id, scheduled_time))`. Only the instance that acquires the lock SHALL execute the job; others SHALL skip it.

#### Scenario: Two nodes, one job
- **WHEN** two application instances both reach a scheduled trigger at the same time
- **THEN** only one instance SHALL execute the job; the other SHALL skip it and log a debug message

#### Scenario: Lock release after execution
- **WHEN** a job execution completes (success or failure)
- **THEN** the advisory lock SHALL be released so the next trigger can proceed

### Requirement: Schedule state machine
Each scheduled task SHALL have a state machine: ACTIVE → PAUSED (user pauses), PAUSED → ACTIVE (user resumes), ACTIVE → COMPLETED (user cancels or end_time reached), ACTIVE → ERROR (execution fails repeatedly). State transitions SHALL be validated.

#### Scenario: Pause a schedule
- **WHEN** a PUT request is sent to `/api/schedules/{id}/pause`
- **THEN** the system SHALL set `state=PAUSED`, stop scheduling the job, and return the updated task

#### Scenario: Resume a schedule
- **WHEN** a PUT request is sent to `/api/schedules/{id}/resume`
- **THEN** the system SHALL set `state=ACTIVE`, recalculate `next_run_at`, and resume scheduling

### Requirement: Schedule management API
The system SHALL expose REST endpoints: `POST /api/schedules` (create), `GET /api/schedules` (list, paginated), `GET /api/schedules/{id}` (get), `PUT /api/schedules/{id}` (update cron/config), `DELETE /api/schedules/{id}` (soft delete), `POST /api/schedules/{id}/trigger` (manual trigger), `PUT /api/schedules/{id}/pause`, `PUT /api/schedules/{id}/resume`, `GET /api/schedules/{id}/executions` (execution history).

#### Scenario: Manual trigger
- **WHEN** a POST request is sent to `/api/schedules/{id}/trigger`
- **THEN** the system SHALL immediately execute the scheduled task regardless of its cron schedule, creating an execution record with `triggered_by="manual"`

#### Scenario: Update cron expression
- **WHEN** a PUT request updates the `cron_expression` of an active schedule
- **THEN** the system SHALL reschedule the APScheduler job and update `next_run_at`

### Requirement: 定时任务管理 REST 访问边界

定时任务管理入口(`/api/schedules` 全部路由)SHALL 施加一致的 workspace、角色与资源归属门:所有入口(含 system scope 身份)要求服务端解析的 active workspace(403 `NO_WORKSPACE`;显式零 UUID workspace 是合法取值,不得当作缺失,也不得退化为全局查询);list/detail/execution history 要求至少 `VIEWER`,create/update/delete/pause/resume/trigger 要求至少 `EDITOR`;角色与 workspace 只取自服务端解析的 `AuthContext`,不接受请求体自报,system 身份在显式 workspace 内保留角色豁免但不获得跨 workspace 查询或操作能力。创建的 workspace/org 归属 SHALL 由服务端实际 workspace 行确定,不得以缺省零 UUID 补造组织归属。

按 ID 入口 SHALL 先以最小查询核对 schedule 存活(存在、未软删除、属于当前 workspace)——缺失、软删除与跨 workspace(含同组织不同 workspace、跨组织)SHALL 使用一致 404,不泄露原因;再施加角色门;然后才读取配置/执行历史、校验 cron/业务参数或修改。不可见父 schedule MUST NOT 进入执行历史查询。历史 `workspace_id=NULL` 的组织级记录 SHALL 原样保留、不迁移不删除,本组 REST 入口不将其隐式开放给任何当前 workspace;后台调度器与可信内部调用保留既有签名兼容,其全局职责不因本边界改变。

创建与更新/resume/trigger SHALL 校验显式绑定的 agent/workflow 存活于目标 workspace(存在、未软删除、workspace 一致;缺失/失效/跨 workspace 使用一致拒绝文案,不区分原因),校验先于落库、scheduler 注册、catch-up 与任何派发副作用;`execution_config` MUST NOT 携带行级归属保留键(org/workspace/agent/workflow 身份);删除与暂停不校验绑定,失效绑定的 schedule 仍可清理。权限或归属拒绝 SHALL 零业务副作用:schedule/execution 行与时间戳不变,scheduler 注册/更新/删除、catch-up 与后台派发不发生;cron/业务参数校验发生在相应权限门之后。当前手动 trigger 仅写执行记录,不构成实际执行成功的认证。

#### Scenario: 跨 workspace 或软删除表现为不存在

- **WHEN** 调用方请求不属于当前 workspace(含同组织其他 workspace 或其他组织)、已软删除或不存在的 schedule 的任一按 ID 入口
- **THEN** 响应为一致 404,不区分三种情形;目标行、其执行记录与 scheduler 状态无任何变化

#### Scenario: 角色不足或缺失被拒且零副作用

- **WHEN** VIEWER 调用任一写入口,或有效身份在当前 workspace 无角色,或缺 workspace(含 system scope)调用任一入口
- **THEN** 响应为 403(缺 workspace 为 `NO_WORKSPACE`),cron 等业务校验不再执行,schedule/execution 行、时间戳与 scheduler 注册不变

#### Scenario: 创建绑定与 execution_config 校验先于副作用

- **WHEN** 创建引用不属于目标 workspace 或已软删除的 agent/workflow,或 `execution_config` 携带 workspace/org/agent/workflow 身份键
- **THEN** 请求被拒绝且文案不区分绑定缺失/失效/跨 workspace,无行落库、无 scheduler 注册

#### Scenario: resume/trigger 复核绑定

- **WHEN** 对绑定的 agent/workflow 已失效(软删除或归属变化)的 schedule 执行 resume 或 trigger
- **THEN** 操作被拒绝,schedule 状态与执行记录不变;delete 与 pause 不受绑定失效影响,仍可用于清理

#### Scenario: 旧组织级记录不隐式开放

- **WHEN** 任何当前 workspace 的成员(含 system 身份)按 ID 或列表访问历史 `workspace_id=NULL` 的组织级 schedule
- **THEN** 表现为不可见(404 或列表不包含),记录本身原样保留

#### Scenario: 授权路径行为保持

- **WHEN** 具备相应角色的成员访问属于当前 workspace 的 schedule
- **THEN** 响应结构、分页与状态码与边界引入前一致;workspace/org 归属取自服务端 workspace 行

### Requirement: Concurrency control with max_concurrent_runs
The system SHALL enforce `max_concurrent_runs` per scheduled task. If a new trigger fires while `max_concurrent_runs` executions are in progress, the system SHALL skip the trigger and log a warning. When `catch_up=true`, the system SHALL queue missed executions for later.

#### Scenario: Max concurrent runs reached
- **WHEN** a scheduled task has `max_concurrent_runs=1` and an execution is still in progress when the next trigger fires
- **THEN** the system SHALL skip the trigger and create an execution record with `status=SKIPPED`

### Requirement: Execution result binding
When a scheduled task triggers an agent or workflow execution, the system SHALL capture the execution result (conversation messages, workflow output) and store a summary in `ScheduledTaskExecution.result_summary`. The execution SHALL run as an authenticated operation using the workspace context of the task creator.

#### Scenario: Agent execution result captured
- **WHEN** a scheduled task triggers an agent execution
- **THEN** the system SHALL create a conversation, execute the agent, and store the response summary in the execution record

### Requirement: 定时 Workflow 执行经平台入口路径

定时任务触发 workflow 执行时,执行 SHALL 经平台入口执行服务完成(与定时 agent 执行同构):登记 Task/Run 关联与身份链、装配治理与事件、产生引擎事件与平台信封;`ScheduledTaskExecution.result_summary` 的结果绑定语义保持不变。测试导向的 workflow 运行器 SHALL 收缩为 studio 测试端点专用,定时路径 MUST NOT 再经其执行。

#### Scenario: 定时 workflow 关联 Task/Run

- **WHEN** 定时任务触发一次 workflow 执行并完成
- **THEN** 该执行登记 Task/Run 关联(按任务创建者 workspace 归属),result_summary 记录输出摘要,引擎事件经入口路径产生

#### Scenario: 定时路径不再经测试运行器

- **WHEN** 检查定时 workflow 执行的代码路径
- **THEN** 其执行经入口执行服务,测试运行器仅被 studio 测试端点使用

### Requirement: 定时 Workflow 输入与租户边界保持

定时 workflow SHALL 将 input_data 作为 workflow channel 输入传递,MUST NOT 将整个输入替换为序列化聊天文本。显式调度 workspace 与 workflow 所属 workspace 不匹配时 SHALL 在执行前拒绝,并使用与不存在目标一致的安全文案。输入 MUST NOT 覆盖运行器的 sys 或内部身份通道。仅有 Task 的当前独立 workflow 关联 SHALL 明确标记缺少 Run,不得声称已完成 Task/Run 登记认证。

#### Scenario: 输入 channel 原样生效

- **WHEN** 调度参数提供 messages 及自定义业务 channel
- **THEN** 执行图读取原始消息和业务值,生成输出基于这些 channel

#### Scenario: 跨 workspace 拒绝

- **WHEN** 调度任务引用其他 workspace 的 workflow
- **THEN** 执行服务和模型/工具不被调用,失败信息不暴露外部 workflow 内容

#### Scenario: 保留系统通道

- **WHEN** input_data 提供 sys 或内部通道值
- **THEN** 请求被拒绝,不能覆盖运行器生成的身份与执行配置
