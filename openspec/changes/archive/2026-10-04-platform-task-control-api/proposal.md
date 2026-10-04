# Proposal

## Why

step6(durable-task-control-plane)已拆为两个并行 worktree:`durable-execution-core`(执行可靠性 + G2 + 独立宿主持久化)与本 change `platform-task-control-api`(平台任务 API + 投影 + 调度接线)。Phase 0 change `durable-execution-contracts`(#215)已定稿共享契约与接缝(`DurableTaskStore` / `ControlCommandRecorder` / `ActionLedger`),但平台侧尚无任何消费方:平台没有任务提交 API、Task 生命周期状态没有持久投影、控制命令没有独立回执记录、治理事件没有版本化 envelope 落地,定时任务调度器 `_execute_task` 至今不执行任何实际工作(直接标记 SUCCESS)。本 change 交付平台控制面的 API、投影表与接线收口,使上述缺口在平台侧闭合,并与 A 轨在同一契约基线上合流。

## What Changes

- 新增**平台任务控制 API**(`channel/api/tasks.py`,挂载 `/api/tasks`):`POST /api/tasks`(幂等提交,成功返回持久化 task_id/run_id;`wait` 参数提供 Task/Run 上的同步等待视图)、`GET /api/tasks`(分页查询)、`GET /api/tasks/{id}`(详情含生命周期与 Run 列表)、`GET /api/runs/{run_id}/events`(游标分页)与 `GET /api/runs/{run_id}/events/stream`(SSE 订阅视图)——同步与流式为同一 Task/Run 上的两个视图。
- 新增**控制命令端点**:`POST /api/tasks/{id}/commands`(cancel/pause/resume/provide_input 统一入口,`POST /api/tasks/{id}/cancel` 为取消糖衣端点)与 `GET /api/commands/{command_id}` 回执查询。HTTP 成功响应只代表 `requested`,绝不显示为 `applied`。
- 新增**待对账查询 API**:`GET /api/reconciliation/pending`——workspace 作用域下处于 `reconciliation_required` 的 Task、`pending_reconciliation` 的 Action 台账记录(A 合入前经 Stub 返回空)与超时未决命令的统一视图;读取时惰性将过期命令收敛为 `expired`。
- 新增**平台投影与命令记录表**(`models/` + alembic):`task_lifecycle_states`(Task 生命周期状态 + revision + 写入方)、`task_submissions`(幂等提交键绑定主体/workspace/请求摘要)、`control_commands`(命令回执记录)、`platform_events`(版本化治理事件 envelope + Run 事件流,带 per-run source_sequence 游标)。
- 新增 **PostgreSQL seam adapter**(`execution/platform_durable.py`):`PostgresDurableTaskStore` 与 `PostgresControlCommandRecorder` 实现既有接缝,注册进参数化契约套件 `DURABLE_IMPLEMENTATIONS`;每次调用使用独立短事务 session。`ActionLedger` 仍绑定 InMemory Stub(A 轨交付真实现)。
- 新增**平台任务控制服务**(`execution/task_control.py`):提交(幂等键 + Task/Run 登记 + 生命周期 queued + 治理事件)、生命周期推进(契约迁移校验)、命令记录与转发、投影更新、治理事件发射(`EventEnvelope` + actor/source profile)。
- **step5 尾巴收口**:调度器 `manager._execute_task` 接线 `ExecutorRegistry`(当前不执行任何内容、直接记 SUCCESS 的空转路径删除),执行结果映射为 success/failed;调度触发经既有 AgentExecutor/WorkflowExecutor 获得真实执行与 Task/Run 关联。
- **composition 接线**(`core/composition/durable_platform.py`):进程级 seam 绑定,开发/测试默认 InMemory Stub,生产绑定平台 PostgreSQL adapter;A 轨合入后同一绑定点切换其核心实现,API/服务层不改。

### 非目标(他轨所有)

- PostgreSQL 作业记录/事务 outbox/独立 worker/租约 fencing token 的执行可靠性核心(`durable-execution-core`);本 change 的内联派发在进程内完成,不宣称跨重启恢复。
- `ActionLedger` 生产实现与 G2 闭环(A 轨);受管投递与状态投影(`managed-runner-enrollment`);审批/授权语义(step7)。
- 独立宿主集成、聊天入口全量事件持久化改造(聊天 SSE 维持现有协议渲染;持久事件读取先服务任务 API 流)。

## Capabilities

### New Capabilities

- `platform-task-control`: 平台任务控制面 API 与投影——幂等提交、Task 生命周期投影、控制命令回执、治理事件发射与游标读取、待对账查询,以及调度器到 executor registry 的接线收口。

### Modified Capabilities

- `platform-entry-execution`: 同步/流式收口补齐——入口事件读取以 Run 引用 + 游标的持久化视图交付(任务 API 流为首个消费方),调度器 `_execute_task` 空转路径删除。

## Impact

- `src/hecate/models/`:新增 `task_lifecycle.py`(lifecycle + submission 两表)、`control_command.py`、`platform_event.py`。
- `alembic/versions/`:新增一个迁移(head `e9a4b72c6d10` 之后)创建四张表;downgrade 删表。
- `src/hecate/execution/`:新增 `platform_durable.py`(seam adapter)、`task_control.py`(应用服务)。
- `src/hecate/channel/api/`:新增 `tasks.py` 路由;`src/hecate/main.py` 增量挂载。
- `src/hecate/core/composition/`:新增 `durable_platform.py` 绑定点。
- `src/hecate/ops/scheduling/manager.py`:`_execute_task` 接线 executor registry。
- `tests/test_execution/`、`tests/test_channel/`、`tests/test_ops/`(或就近目录):seam adapter 契件套件注册、服务与 API 测试、调度接线测试。
- 无破坏性 API 变更;`scheduled_task_executions` 的 result_summary 开始携带真实执行结果。
