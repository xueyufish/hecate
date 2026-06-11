## ADDED Requirements

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
