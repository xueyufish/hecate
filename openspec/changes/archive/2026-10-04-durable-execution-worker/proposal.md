# Proposal

## Why

step6 两轨并行交付后(#216 `durable-execution-core`、#217 `platform-task-control-api`),平台任务控制面仍以进程内派发运行:composition 在 postgres 后端下 `ActionLedger` 仍绑 InMemory Stub,`TaskControlService` 派发为进程内执行(重启丢失,两轨 design 注记明确"worker 合入后切换""此窗口由 outbox 消除")。演进方案 step6 清单尚有 9 项未勾,其中"事务 outbox 和独立 worker""关键状态与 outbox 同事务提交""等待外部事件或人工输入的回调契约"与外部调度器双主验证,都是本 change 的交付物;它们同时是独立交付最短路径(I-Ca)中"重启恢复/对账"验收的基础设施。

## What Changes

- 新增持久派发 worker(参考实现):按租约/fencing 认领 `queued` 任务并投递给注入的 dispatcher;启动对账(queued 重派、过期租约重派、活跃租约不动);有界重试(次数上限 + 退避,放弃进入 `reconciliation_required`);优雅 drain。
- composition 的 `postgres` 后端整体切换到 `SqlDurableStore`(三接缝一体,单引擎单事务域);`platform_durable.py` 的 `PostgresDurableTaskStore`/`PostgresControlCommandRecorder` 与其三张表退役。**BREAKING**:迁移删除 `task_lifecycle_states`/`task_submissions`/`control_commands`(两轨同日合入、无生产部署,表内无需保留数据);`platform_events` 保留为读模型。
- 平台任务控制面接入 worker:`TaskControlService._dispatch` 成为注入 worker 的执行回调,跨重启后台执行保证成立并移除"进程内派发、不宣称跨重启"的限定;进程内派发模式退役,stub 后端仅用于开发/测试。
- 治理事件单写者收敛:seam 状态写入同事务追加 envelope(权威 outbox,phase-0 `validate_governance_event` 校验);`platform_events` 由 outbox 中继按游标有界重试投影;`task_control` 直发 emit 点按"是否绑定 seam 状态迁移"逐点改造。
- `waiting_input`/`waiting_approval` 持久等待机制:`provide_input`/`resolve_approval` 命令携带单次消费回调 token 与期限,迟到/重复/终态后拒绝;重启后等待可恢复,输入随任务状态重放派发。审批策略与参数绑定校验归 step7,本 change 只交付持久化机制。
- 外部调度器双主验证:APScheduler job 状态与平台任务状态各自单写入方,租约过期后旧 ownership 写入被 fencing 拒绝,以测试钉住。
- 最小编排场景:确定性工作流经平台 Task/Run 接口启动 Agent 子任务并持久等待其完成(委派/验收语义归 step13)。

## Capabilities

### New Capabilities

- `durable-execution-worker`:持久派发 worker 参考实现——租约/fencing 认领、重启对账、有界重试与 drain、outbox 中继投影、独立进程入口;平台与 runner 共用同一 worker 库。

### Modified Capabilities

- `platform-task-control`:提交要求从"进程内派发、不宣称跨重启"改为经持久 worker 派发并宣称跨重启保证;新增"等待人工输入或审批为持久等待并可恢复"要求;调度器要求增补双主约束场景。

## Impact

- `packages/hecate-durable`:新增 `worker.py`(派发循环、对账、重试、drain)与 worker-only 入口;`storage/` 复用既有 `LeaseManager`/fencing,不改契约与接缝。
- `src/hecate/execution/`:`task_control.py`(派发回调化、等待机制、emit 点收敛)、`platform_durable.py`(adapter 与表退役)、`governance_events.py`(变为投影写入门户)。
- `src/hecate/core/composition/durable_platform.py`:postgres 后端绑定 `SqlDurableStore` 三接缝。
- `src/hecate/ops/scheduling/`:双主验证测试(实现不改,`manager.py` 仅在验证暴露问题时调整)。
- Alembic:新增迁移(创建 hecate-durable 表、删除三张退役表);主 spec `durable-execution-storage`、`durable-execution-contract` 不改(契约与存储语义不变,worker 是新增能力)。
- 测试:`tests/test_execution/`(worker/对账/重试/中继/等待)、`tests/test_channel/test_task_control_api.py`(API 语义更新)、调度器双主测试。
- 状态翻转:SC03 的持久化/重启半边获得基础设施;manifest 翻转仍归 step7(审批语义)/step10(SC06),本 change 不动 manifest。
