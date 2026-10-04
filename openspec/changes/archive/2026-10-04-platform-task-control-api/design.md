# Design

## Context

Phase 0(`durable-execution-contracts`,#215)已定稿契约层(`contracts/execution/durable.py`)与同步接缝(`execution/durable.py`:`DurableTaskStore` / `ControlCommandRecorder` / `ActionLedger`)及 InMemory Stub,并在 `tests/test_execution/conftest.py` 预留了参数化契约套件注册点——注释明确本 worktree(B)注册"platform adapter over this seam"。平台侧已有资产:`TaskRunRegistry`(tasks/runs/enrollment/link 的单写入方,AsyncSession)、`EntryExecutionService`(入口执行 + Task/Run 关联,fail-open)、`RunEventMapper`(引擎流 → envelope,内存保留)、`ExecutorRegistry`(agent/workflow 执行器,但调度器 `manager._execute_task` 从未调用它,当前空转记 SUCCESS)。调度器为 APScheduler + PostgreSQL job store。

两轨约束:A 轨(`durable-execution-core`)交付 worker/租约/outbox 执行可靠性与 `ActionLedger` 生产实现;本轨先以进程内派发 + Stub 台账运行,composition 单点切换。

## Goals / Non-Goals

**Goals:**

- 平台任务控制 API 全量落地(提交/查询/事件/命令/待对账),语义严格来自 Phase 0 契约,不新造词汇。
- 投影与命令表单写入方、独立短事务;seam adapter 注册契约套件全量通过。
- 调度器空转路径删除,触发真实执行。
- composition 绑定点唯一,A 轨合入后一处切换。

**Non-Goals:**

- 跨重启后台执行保证、outbox、租约 fencing(A 轨)。
- `ActionLedger` 生产实现(A 轨);本轨命令/生命周期不依赖台账。
- 聊天入口事件持久化改造(聊天 SSE 维持现有渲染;持久事件读取先服务任务 API 流)。
- 受管投递/投影(`managed-runner-enrollment`)、审批与授权语义(step7)。

## Decisions

### D1. 生命周期/命令走 seam adapter(平台 Postgres 实现),治理事件走独立异步服务

`PostgresDurableTaskStore` / `PostgresControlCommandRecorder` 实现同步接缝 ABC,底层用独立 **sync engine**(同一 DATABASE_URL,aiosqlite→sqlite 映射),每次调用一个短 session 一个事务——满足"每个数据库事务使用独立 session"。异步 `TaskControlService` 经 `asyncio.to_thread` 调用,避免阻塞事件循环。治理事件(`platform_events`)不是接缝的一部分,直接以 AsyncSession 服务(发射常与 API 请求事务同链)。

理由:契约套件是同步的,adapter 必须同步;平台主 ORM 是 async,混用两个 engine 指向同一库是可接受的(SQLAlchemy 原生支持),换来的是套件断言免费继承 + A 轨实现可整体替换。备选"全 async 自研服务绕过接缝"被否:会绕开契约套件,且 A 轭合入后仍要改一次。

### D2. 表布局:四张新表,不动 tasks/runs 既有列

- `task_lifecycle_states`:(issuer_domain, task_id) 唯一,lifecycle_state/revision/recorded_at/writer_source/workspace_id。独立于 `tasks` 责任记录——接缝按 task_ref(签发域+ID)寻址,自包含;避免改动单写入方 registry 的热表;A 轨实现可另行落表不冲突。
- `task_submissions`:idempotency_key 唯一,subject/workspace/request_digest + task/run 引用。
- `control_commands`:command_id 唯一,契约 `ControlCommandRecord` 全字段 + workspace_id。
- `platform_events`:event_id 唯一,契约 envelope 全字段(envelope JSON 整体保存 + 关键列展开),per-run (workspace, run_id, source_sequence) 索引支撑游标分页。

理由:每张表一个写入方;生命周期若做成 `tasks` 新列会让 registry 与控制服务同表争锁,且契约按 ref 寻址的语义对不上。

### D3. 提交流程与派发模式

提交:校验 workspace/agent/deployment → `record_submission`(幂等仲裁)→ `TaskRunRegistry.create_task + create_run`(一个 async 事务,backend_ref 指向预铸造的引擎 session UUID)→ `apply_task_state(queued)` → 治理事件 `task.submitted`。派发模式由 composition 提供,本轨交付"inline":wait=true 在请求内等待终态;wait=false 经 `asyncio.create_task` 后台执行(进程内存活,断开 HTTP 不中断;重启丢失执行——诚实标注,A 轨 worker 合入后切换)。inline 执行复用 AgentExecutor 的装配模式(独立 db session、entry service、correlation 用 `existing_task_id` 挂到已建 Task),流式路径用 `RunEventMapper` 映射并逐条持久追加。

多 store 非原子性(task+run 落库后 lifecycle 写失败)风险:提交返回 5xx,Task 行存在但无 lifecycle 行;查询侧 lifecycle 缺失显式报告 `unrecorded`,不猜测。A 轨 outbox 合入后此窗口消除。

### D4. 命令端点与回执语义

统一入口 `POST /api/tasks/{id}/commands`(kind: cancel/pause/resume/provide_input)+ `/cancel` 糖衣。流程:registry 校验 task/workspace → `ControlCommandRecorder.record`(requested)→ cancel 转发 `request_cancel` 至该 Task 活跃 Run 的 backend(builtin backend 经 `HecateExecutionBackend`;pause/resume/provide_input 能力未声明时记录 rejected)→ 治理事件。回执迁移(acknowledged/applied)由执行侧回执路径调用 `transition`;读取时对过期命令惰性收敛 `expired`(读改写仅限该收敛,对账 API 同样只读)。HTTP 响应永远是回执记录本身。

### D5. 调度器接线

`_execute_task` 在咨询锁与并发检查后:按 ScheduledTask 行的 agent_id/workflow_id 解析目标类型 → `create_default_registry()`(进程级缓存)取 executor → `executor.execute(task_id, execution_config, workspace_id=..., agent_id=..., workflow_id=...)` → 结果 status 映射 success/failed,`result_summary`/`error_message` 落 `scheduled_task_executions`。空转路径删除。executors 模块已在 B 独占文件面内,不改 `TaskExecutor` ABC。

### D6. composition 绑定点

`core/composition/durable_platform.py`:`get_durable_suite()` 进程单例,settings 开关 `HECATE_DURABLE_BACKEND`(stub|postgres,默认 stub;生产部署文档指引切 postgres)。返回 (store, recorder, ledger);postgres 模式下 ledger 仍为 InMemory Stub(A 轨交付后替换),响应/API 显式标注台账来源。API 与服务层只 import 接缝 ABC 与 composition 工厂。

## Risks / Trade-offs

- [sync/async 双 engine 并存] → adapter 每调用独立 session、无共享状态;连接池小上限;测试用 sqlite 内存库验证两 engine 同库。
- [inline 派发无跨重启保证] → API 与文档显式标注开发期语义;派发模式经 composition,A 轨切换零改动。
- [多 store 非原子窗口] → lifecycle 缺失显式 `unrecorded`;治理事件兜底记录失败原因。
- [SSE 长连接占用] → 游标轮询间隔可配;断线以游标恢复,不依赖连接存活。
- [Stub 台账在待对账 API 中虚报] → 响应显式 `ledger_source: stub`,空列表不冒充生产扫描。

## Migration Plan

一个 alembic 迁移(revision 链于 `e9a4b72c6d10` 后)建四表;upgrade 幂等建表,downgrade 删四表(新表无历史数据风险)。无数据回填。回滚应用保留表(与 step4 惯例一致)。部署顺序:迁移 → 新代码;旧版本不读新表,向前兼容。

## Open Questions

- 治理事件保留/导出策略(留存期限、对象存储导出)——step10/16 范围,本轨只保证可查询与游标读取。
- 命令回执的执行侧 `applied` 上报路径(builtin backend 的取消生效回执)——本轨 builtin 取消停留在 requested/acknowledged + 引擎取消请求,applied 回执随 A 轨回执通道补齐。
